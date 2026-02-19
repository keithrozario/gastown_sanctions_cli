"""
OFAC SDN Dataflow Pipeline (Apache Beam / Flex Template)

Reads the OFAC SDN Advanced XML from Google Cloud Storage,
parses it into flat BigQuery rows, and writes them to BigQuery
using WRITE_TRUNCATE (full refresh on every run).

Usage (direct):
  python pipeline.py \
    --runner=DataflowRunner \
    --project=<project> \
    --region=<region> \
    --gcs_path=gs://<bucket>/raw/SDN_ADVANCED_<date>.XML \
    --bq_table=<project>:<dataset>.<table> \
    --staging_location=gs://<bucket>/staging \
    --temp_location=gs://<bucket>/temp \
    --service_account_email=<sa>@<project>.iam.gserviceaccount.com \
    --dataflow_service_options=enable_prime

Usage (via Flex Template):
  Called by the Cloud Function after XML download via Dataflow REST API.
"""

import argparse
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import apache_beam as beam
from apache_beam.io.gcp import bigquery
from apache_beam.options.pipeline_options import (
    PipelineOptions,
    SetupOptions,
)
from google.cloud import storage as gcs


# ── Inlined XML parser (avoids Dataflow module distribution issues) ───────────

OFAC_SOURCE_URL = (
    "https://sanctionslistservice.ofac.treas.gov"
    "/api/PublicationPreview/exports/SDN_ADVANCED.XML"
)

NAME_PART_ORDER = {
    "last name": 0, "last": 0, "entity name": 0, "vessel name": 0, "aircraft name": 0,
    "first name": 1, "first": 1, "middle name": 2, "middle": 2,
    "patronymic": 3, "matronymic": 4,
}

VESSEL_FEATURES = {
    "vessel call sign": "vessel_call_sign", "vessel type": "vessel_type",
    "vessel tonnage": "vessel_tonnage", "gross registered tonnage": "vessel_grt",
    "vessel flag": "vessel_flag", "vessel owner": "vessel_owner",
    "mmsi": "vessel_mmsi", "imo": "vessel_imo",
}

AIRCRAFT_FEATURES = {
    "aircraft construction number": "aircraft_serial",
    "aircraft manufacturer's serial number": "aircraft_serial",
    "aircraft model": "aircraft_type", "aircraft operator": "aircraft_operator",
    "aircraft tail number": "aircraft_tail_number", "aircraft type": "aircraft_type",
    "aircraft manufacturer": "aircraft_manufacturer",
}


def _local_tag(element_tag):
    return element_tag.split("}")[-1] if "}" in element_tag else element_tag


def _iter_tag(parent, local_name):
    for child in parent:
        if _local_tag(child.tag) == local_name:
            yield child


def _find_tag(parent, local_name):
    for child in parent:
        if _local_tag(child.tag) == local_name:
            return child
    return None


def _parse_date_period(dp_elem):
    for boundary in dp_elem:
        if _local_tag(boundary.tag) not in ("Start", "End"):
            continue
        from_elem = _find_tag(boundary, "From")
        if from_elem is None:
            continue
        parts = {_local_tag(d.tag): (d.text or "").strip() for d in from_elem}
        year = parts.get("Year", "")
        if not year:
            continue
        month = parts.get("Month", "").zfill(2) if parts.get("Month") else ""
        day = parts.get("Day", "").zfill(2) if parts.get("Day") else ""
        if year and month and day:
            return f"{year}-{month}-{day}"
        elif year and month:
            return f"{year}-{month}"
        elif year:
            return year
    return None


def _build_ref_maps(root):
    refs = {}
    for child in root:
        if _local_tag(child.tag) != "ReferenceValueSets":
            continue
        raw_party_subtypes = {}  # id -> (text, party_type_id) — for cross-referencing
        for set_elem in child:
            set_name = _local_tag(set_elem.tag)
            mapping = {}
            if set_name == "LegalBasisValues":
                for lb in set_elem:
                    lb_id = lb.get("ID")
                    if not lb_id:
                        continue
                    short_ref = _find_tag(lb, "LegalBasisShortRef")
                    mapping[lb_id] = (short_ref.text or "").strip() if short_ref else ""
            elif set_name == "PartySubTypeValues":
                # PartySubType items have a PartyTypeID attribute; "Unknown" entries
                # need to be resolved via PartyTypeValues (Individual/Entity/etc.)
                for item in set_elem:
                    item_id = item.get("ID")
                    if item_id:
                        raw_party_subtypes[item_id] = (
                            (item.text or "").strip(),
                            item.get("PartyTypeID", ""),
                        )
                mapping = {k: v[0] for k, v in raw_party_subtypes.items()}
            else:
                for item in set_elem:
                    item_id = item.get("ID")
                    if item_id:
                        mapping[item_id] = (item.text or "").strip()
            refs[set_name] = mapping
        # Cross-reference PartySubTypeValues with PartyTypeValues now that both are built
        if raw_party_subtypes and "PartyTypeValues" in refs:
            party_type_values = refs["PartyTypeValues"]
            refs["PartySubTypeValues"] = {
                sub_id: (sub_text if sub_text and sub_text != "Unknown"
                         else party_type_values.get(party_type_id, sub_text))
                for sub_id, (sub_text, party_type_id) in raw_party_subtypes.items()
            }
        break
    return refs


def _build_locations_map(root, country_values, loc_part_types):
    locations = {}
    for child in root:
        if _local_tag(child.tag) != "Locations":
            continue
        for loc in child:
            if _local_tag(loc.tag) != "Location":
                continue
            loc_id = loc.get("ID")
            if not loc_id:
                continue
            loc_data = {"address": "", "city": "", "state_province": "",
                        "postal_code": "", "country": "", "region": ""}
            for lchild in loc:
                lt = _local_tag(lchild.tag)
                if lt == "LocationCountry":
                    loc_data["country"] = country_values.get(lchild.get("CountryID"), "")
                elif lt == "LocationPart":
                    loc_part_name = loc_part_types.get(lchild.get("LocPartTypeID"), "").lower()
                    part_value = ""
                    for lpv in lchild:
                        if _local_tag(lpv.tag) == "LocationPartValue":
                            part_value = (lpv.text or "").strip()
                            break
                    if not part_value:
                        continue
                    if "city" in loc_part_name:
                        loc_data["city"] = part_value
                    elif "address" in loc_part_name:
                        loc_data["address"] = part_value
                    elif "state" in loc_part_name or "province" in loc_part_name:
                        loc_data["state_province"] = part_value
                    elif "postal" in loc_part_name or "zip" in loc_part_name:
                        loc_data["postal_code"] = part_value
                    elif "region" in loc_part_name:
                        loc_data["region"] = part_value
                    else:
                        loc_data["address"] = (loc_data["address"] + ", " + part_value).lstrip(", ")
            locations[loc_id] = loc_data
        break
    return locations


def _build_id_docs_map(root, country_values, id_reg_doc_types):
    docs = {}
    for child in root:
        if _local_tag(child.tag) != "IDRegDocuments":
            continue
        for doc in child:
            if _local_tag(doc.tag) != "IDRegDocument":
                continue
            doc_id = doc.get("ID")
            if not doc_id:
                continue
            doc_data = {
                "id_type": id_reg_doc_types.get(doc.get("IDRegDocTypeID", ""), ""),
                "id_number": "", "country": "", "issue_date": "",
                "expiry_date": "", "is_fraudulent": False,
            }
            for dchild in doc:
                dct = _local_tag(dchild.tag)
                if dct == "IDRegDocType":
                    doc_data["id_type"] = id_reg_doc_types.get(
                        dchild.get("IDRegDocTypeID"), (dchild.text or "").strip())
                elif dct == "IDRegDocumentID":
                    doc_data["id_number"] = (dchild.text or "").strip()
                elif dct == "IssuingCountry":
                    doc_data["country"] = country_values.get(dchild.get("CountryID"), "")
                elif dct == "IDRegDocDateOfIssuance":
                    doc_data["issue_date"] = _parse_date_period(dchild) or ""
                elif dct == "IDRegDocExpirationDate":
                    doc_data["expiry_date"] = _parse_date_period(dchild) or ""
            docs[doc_id] = doc_data
        break
    return docs


def _build_sanctions_map(root, legal_basis_map, sanctions_programs):
    sanctions = {}
    for child in root:
        if _local_tag(child.tag) != "SanctionsEntries":
            continue
        for entry in child:
            if _local_tag(entry.tag) != "SanctionsEntry":
                continue
            # ProfileID is an attribute of SanctionsEntry, not a child element
            profile_id = entry.get("ProfileID", "").strip()
            if not profile_id:
                continue
            programs = []
            legal_authorities = []
            remarks = ""
            for echild in entry:
                ect = _local_tag(echild.tag)
                if ect == "SanctionsMeasure":
                    # Program name is in the Comment child of SanctionsMeasure
                    for sm_child in echild:
                        smct = _local_tag(sm_child.tag)
                        if smct == "Comment":
                            prog_name = (sm_child.text or "").strip()
                            if prog_name and prog_name not in programs:
                                programs.append(prog_name)
                elif ect == "EntryEvent":
                    # LegalBasisID is an attribute of EntryEvent itself
                    lb_id = echild.get("LegalBasisID", "")
                    if lb_id and lb_id in legal_basis_map:
                        la = legal_basis_map[lb_id]
                        if la and la not in legal_authorities:
                            legal_authorities.append(la)
                elif ect == "Remarks":
                    remarks = (echild.text or "").strip()
            if profile_id not in sanctions:
                sanctions[profile_id] = {"programs": [], "legal_authorities": [], "remarks": ""}
            se = sanctions[profile_id]
            se["programs"].extend(p for p in programs if p not in se["programs"])
            se["legal_authorities"].extend(la for la in legal_authorities if la not in se["legal_authorities"])
            if remarks:
                se["remarks"] = remarks
        break
    return sanctions


def _parse_identity(identity_elem, record, alias_types, script_values, name_part_types):
    npg_map = {}
    for child in identity_elem:
        if _local_tag(child.tag) != "NamePartGroups":
            continue
        for master in child:
            for ng in master:
                if _local_tag(ng.tag) == "NamePartGroup":
                    ng_id = ng.get("ID")
                    ng_type_id = ng.get("NamePartTypeID")
                    if ng_id and ng_type_id:
                        npg_map[ng_id] = name_part_types.get(ng_type_id, f"part_{ng_type_id}")
    for alias in _iter_tag(identity_elem, "Alias"):
        alias_type_id = alias.get("AliasTypeID", "")
        is_primary = alias.get("Primary", "false").strip().lower() == "true"
        is_low_quality = alias.get("LowQuality", "false").strip().lower() == "true"
        alias_type = alias_types.get(alias_type_id, "a.k.a.")
        alias_quality = "weak" if is_low_quality else "strong"
        for doc_name in _iter_tag(alias, "DocumentedName"):
            parts_raw = []
            for dnp in _iter_tag(doc_name, "DocumentedNamePart"):
                for npv in _iter_tag(dnp, "NamePartValue"):
                    group_id = npv.get("NamePartGroupID", "")
                    script_id = npv.get("ScriptID", "")
                    part_type = npg_map.get(group_id, "Name")
                    script = script_values.get(script_id, "")
                    value = (npv.text or "").strip()
                    if value:
                        sort_key = NAME_PART_ORDER.get(part_type.lower(), 99)
                        parts_raw.append((sort_key, part_type, script, value))
            parts_raw.sort(key=lambda x: x[0])
            name_parts_bq = [{"part_type": pt, "part_value": pv, "script": sc}
                             for _, pt, sc, pv in parts_raw]
            full_name = " ".join(pv for _, _, _, pv in parts_raw if pv)
            if not full_name:
                continue
            if is_primary and record["primary_name"] is None:
                record["primary_name"] = {"full_name": full_name, "name_parts": name_parts_bq}
            else:
                record["aliases"].append({"alias_type": alias_type, "alias_quality": alias_quality,
                                          "full_name": full_name, "name_parts": name_parts_bq})


def _apply_location(loc, ft_name, record):
    if "birth" in ft_name and "place" in ft_name:
        pob = ", ".join(filter(None, [loc.get("city"), loc.get("state_province"), loc.get("country")]))
        if pob and pob not in record["places_of_birth"]:
            record["places_of_birth"].append(pob)
    else:
        addr_entry = {
            "address": loc.get("address", ""), "city": loc.get("city", ""),
            "state_province": loc.get("state_province", ""), "postal_code": loc.get("postal_code", ""),
            "country": loc.get("country", ""), "region": loc.get("region", ""),
        }
        if any(addr_entry.values()):
            record["addresses"].append(addr_entry)


def _parse_features(profile_elem, record, feature_types, country_values, locations_map, id_docs_map):
    vessel = {}
    aircraft = {}
    additional_sanctions = []
    for feature in _iter_tag(profile_elem, "Feature"):
        feature_type_id = feature.get("FeatureTypeID", "")
        ft_name = feature_types.get(feature_type_id, "").lower()
        for fv in _iter_tag(feature, "FeatureVersion"):
            comment = ""
            for fvc in fv:
                fvct = _local_tag(fvc.tag)
                if fvct == "Comment":
                    comment = (fvc.text or "").strip()
                elif fvct == "DatePeriod":
                    date_val = _parse_date_period(fvc)
                    if date_val and "birth" in ft_name and "date" in ft_name:
                        if date_val not in record["dates_of_birth"]:
                            record["dates_of_birth"].append(date_val)
                elif fvct == "VersionDetail":
                    country_id = fvc.get("CountryID", "")
                    if country_id:
                        country_name = country_values.get(country_id, "")
                        if "national" in ft_name and country_name:
                            if country_name not in record["nationalities"]:
                                record["nationalities"].append(country_name)
                        elif "citizen" in ft_name and country_name:
                            if country_name not in record["citizenships"]:
                                record["citizenships"].append(country_name)
                    for vdc in fvc:
                        vdct = _local_tag(vdc.tag)
                        if vdct == "LocationID":
                            loc_id = (vdc.text or "").strip()
                            if loc_id and loc_id in locations_map:
                                _apply_location(locations_map[loc_id], ft_name, record)
                        elif vdct == "IDRegDocumentReference":
                            doc_id = vdc.get("DocumentID", "")
                            if doc_id and doc_id in id_docs_map:
                                record["id_documents"].append(dict(id_docs_map[doc_id]))
                elif fvct == "VersionLocation":
                    loc_id = fvc.get("LocationID", "")
                    if loc_id and loc_id in locations_map:
                        _apply_location(locations_map[loc_id], ft_name, record)
            if "gender" in ft_name and comment:
                record["gender"] = comment
            elif "title" in ft_name and comment:
                record["title"] = comment
            elif "additional sanctions" in ft_name and comment:
                additional_sanctions.append(comment)
            for vessel_key, field_name in VESSEL_FEATURES.items():
                if vessel_key in ft_name and comment:
                    vessel[field_name] = comment
                    break
            for aircraft_key, field_name in AIRCRAFT_FEATURES.items():
                if aircraft_key in ft_name and comment:
                    aircraft[field_name] = comment
                    break
    if vessel:
        record["vessel_info"] = {
            "vessel_type": vessel.get("vessel_type"), "vessel_flag": vessel.get("vessel_flag"),
            "vessel_owner": vessel.get("vessel_owner"), "vessel_tonnage": vessel.get("vessel_tonnage"),
            "vessel_grt": vessel.get("vessel_grt"), "vessel_call_sign": vessel.get("vessel_call_sign"),
            "vessel_mmsi": vessel.get("vessel_mmsi"), "vessel_imo": vessel.get("vessel_imo"),
        }
    if aircraft:
        record["aircraft_info"] = {
            "aircraft_type": aircraft.get("aircraft_type"), "aircraft_manufacturer": aircraft.get("aircraft_manufacturer"),
            "aircraft_serial": aircraft.get("aircraft_serial"), "aircraft_tail_number": aircraft.get("aircraft_tail_number"),
            "aircraft_operator": aircraft.get("aircraft_operator"),
        }
    if additional_sanctions:
        record["additional_sanctions_info"] = "; ".join(additional_sanctions)


def parse_ofac_advanced_xml(xml_bytes):
    logger.info("Parsing OFAC Advanced XML (%d bytes)", len(xml_bytes))
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Failed to parse XML: {exc}") from exc

    pub_date = None
    for child in root:
        if _local_tag(child.tag) == "DateOfIssue":
            pub_date = (child.text or "").strip()
            break

    logger.info("OFAC publication date: %s", pub_date)
    refs = _build_ref_maps(root)
    alias_types = refs.get("AliasTypeValues", {})
    party_subtypes = refs.get("PartySubTypeValues", {})
    feature_types = refs.get("FeatureTypeValues", {})
    script_values = refs.get("ScriptValues", {})
    name_part_types = refs.get("NamePartTypeValues", {})
    country_values = refs.get("CountryValues", {})
    loc_part_types = refs.get("LocPartTypeValues", {})
    id_reg_doc_types = refs.get("IDRegDocTypeValues", {})
    sanctions_programs = refs.get("SanctionsProgramValues", {})
    legal_basis_map = refs.get("LegalBasisValues", {})

    locations_map = _build_locations_map(root, country_values, loc_part_types)
    id_docs_map = _build_id_docs_map(root, country_values, id_reg_doc_types)
    sanctions_map = _build_sanctions_map(root, legal_basis_map, sanctions_programs)

    logger.info("Lookup maps: %d locations, %d id_docs, %d sanctions",
                len(locations_map), len(id_docs_map), len(sanctions_map))

    ingestion_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    records = []

    for child in root:
        if _local_tag(child.tag) != "DistinctParties":
            continue
        for dp in child:
            if _local_tag(dp.tag) != "DistinctParty":
                continue
            fixed_ref = dp.get("FixedRef", "").strip()
            if not fixed_ref:
                continue
            record = {
                "sdn_entry_id": int(fixed_ref), "sdn_type": None,
                "programs": [], "legal_authorities": [],
                "primary_name": None, "aliases": [], "addresses": [],
                "id_documents": [], "dates_of_birth": [], "places_of_birth": [],
                "nationalities": [], "citizenships": [],
                "title": None, "gender": None, "remarks": None,
                "vessel_info": None, "aircraft_info": None,
                "additional_sanctions_info": None,
                "publication_date": pub_date,
                "ingestion_timestamp": ingestion_ts,
                "source_url": OFAC_SOURCE_URL,
            }
            for dp_child in dp:
                dp_ct = _local_tag(dp_child.tag)
                if dp_ct == "Profile":
                    profile_id = dp_child.get("ID", "")
                    record["sdn_type"] = party_subtypes.get(dp_child.get("PartySubTypeID", ""))
                    if profile_id in sanctions_map:
                        se = sanctions_map[profile_id]
                        record["programs"] = list(se.get("programs", []))
                        record["legal_authorities"] = list(se.get("legal_authorities", []))
                        record["remarks"] = se.get("remarks") or None
                    for pchild in dp_child:
                        if _local_tag(pchild.tag) == "Identity":
                            _parse_identity(pchild, record, alias_types, script_values, name_part_types)
                    _parse_features(dp_child, record, feature_types, country_values, locations_map, id_docs_map)
            records.append(record)
        break

    logger.info("Parsed %d DistinctParty records", len(records))
    return pub_date, records

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ParseOfacXmlFn(beam.DoFn):
    """
    DoFn that reads the full OFAC XML from GCS, parses it,
    and yields one BigQuery row dict per DistinctParty.

    Accepts a GCS URI string (gs://bucket/path/file.xml) as input element.
    """

    def process(self, gcs_uri: str):
        logger.info("Downloading XML from %s", gcs_uri)

        # Download directly from GCS using the google-cloud-storage library
        client = gcs.Client()
        bucket_name, blob_name = gcs_uri[len("gs://"):].split("/", 1)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        xml_bytes = blob.download_as_bytes()

        logger.info("Downloaded %d bytes, starting parse", len(xml_bytes))
        pub_date, records = parse_ofac_advanced_xml(xml_bytes)
        logger.info("Parse complete: %d records (pub_date=%s)", len(records), pub_date)

        for record in records:
            yield record


def clean_record(record: dict) -> dict:
    """
    Post-process a parsed record to ensure BigQuery compatibility:
    - Remove fields that are entirely None/empty nested structs
    - Ensure RECORD fields with all-None values are set to None (not {})
    """
    def _strip_empty_struct(d):
        if not isinstance(d, dict):
            return d
        cleaned = {k: _strip_empty_struct(v) for k, v in d.items()}
        # If every value in the struct is None or empty string, treat as null
        if all(v is None or v == "" for v in cleaned.values()):
            return None
        return cleaned

    # Nullable RECORD fields
    for field in ("vessel_info", "aircraft_info", "primary_name"):
        if record.get(field) is not None:
            record[field] = _strip_empty_struct(record[field])

    return record


class CleanRecordFn(beam.DoFn):
    def process(self, record: dict):
        yield clean_record(record)


def build_bq_schema():
    """Return the BigQuery schema JSON string matching bigquery.tf."""
    schema_fields = [
        {"name": "sdn_entry_id", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "sdn_type", "type": "STRING", "mode": "NULLABLE"},
        {"name": "programs", "type": "STRING", "mode": "REPEATED"},
        {"name": "legal_authorities", "type": "STRING", "mode": "REPEATED"},
        {
            "name": "primary_name",
            "type": "RECORD",
            "mode": "NULLABLE",
            "fields": [
                {"name": "full_name", "type": "STRING", "mode": "NULLABLE"},
                {
                    "name": "name_parts",
                    "type": "RECORD",
                    "mode": "REPEATED",
                    "fields": [
                        {"name": "part_type", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "part_value", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "script", "type": "STRING", "mode": "NULLABLE"},
                    ],
                },
            ],
        },
        {
            "name": "aliases",
            "type": "RECORD",
            "mode": "REPEATED",
            "fields": [
                {"name": "alias_type", "type": "STRING", "mode": "NULLABLE"},
                {"name": "alias_quality", "type": "STRING", "mode": "NULLABLE"},
                {"name": "full_name", "type": "STRING", "mode": "NULLABLE"},
                {
                    "name": "name_parts",
                    "type": "RECORD",
                    "mode": "REPEATED",
                    "fields": [
                        {"name": "part_type", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "part_value", "type": "STRING", "mode": "NULLABLE"},
                        {"name": "script", "type": "STRING", "mode": "NULLABLE"},
                    ],
                },
            ],
        },
        {
            "name": "addresses",
            "type": "RECORD",
            "mode": "REPEATED",
            "fields": [
                {"name": "address", "type": "STRING", "mode": "NULLABLE"},
                {"name": "city", "type": "STRING", "mode": "NULLABLE"},
                {"name": "state_province", "type": "STRING", "mode": "NULLABLE"},
                {"name": "postal_code", "type": "STRING", "mode": "NULLABLE"},
                {"name": "country", "type": "STRING", "mode": "NULLABLE"},
                {"name": "region", "type": "STRING", "mode": "NULLABLE"},
            ],
        },
        {
            "name": "id_documents",
            "type": "RECORD",
            "mode": "REPEATED",
            "fields": [
                {"name": "id_type", "type": "STRING", "mode": "NULLABLE"},
                {"name": "id_number", "type": "STRING", "mode": "NULLABLE"},
                {"name": "country", "type": "STRING", "mode": "NULLABLE"},
                {"name": "issue_date", "type": "STRING", "mode": "NULLABLE"},
                {"name": "expiry_date", "type": "STRING", "mode": "NULLABLE"},
                {"name": "is_fraudulent", "type": "BOOLEAN", "mode": "NULLABLE"},
            ],
        },
        {"name": "dates_of_birth", "type": "STRING", "mode": "REPEATED"},
        {"name": "places_of_birth", "type": "STRING", "mode": "REPEATED"},
        {"name": "nationalities", "type": "STRING", "mode": "REPEATED"},
        {"name": "citizenships", "type": "STRING", "mode": "REPEATED"},
        {"name": "title", "type": "STRING", "mode": "NULLABLE"},
        {"name": "gender", "type": "STRING", "mode": "NULLABLE"},
        {"name": "remarks", "type": "STRING", "mode": "NULLABLE"},
        {
            "name": "vessel_info",
            "type": "RECORD",
            "mode": "NULLABLE",
            "fields": [
                {"name": "vessel_type", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_flag", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_owner", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_tonnage", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_grt", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_call_sign", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_mmsi", "type": "STRING", "mode": "NULLABLE"},
                {"name": "vessel_imo", "type": "STRING", "mode": "NULLABLE"},
            ],
        },
        {
            "name": "aircraft_info",
            "type": "RECORD",
            "mode": "NULLABLE",
            "fields": [
                {"name": "aircraft_type", "type": "STRING", "mode": "NULLABLE"},
                {"name": "aircraft_manufacturer", "type": "STRING", "mode": "NULLABLE"},
                {"name": "aircraft_serial", "type": "STRING", "mode": "NULLABLE"},
                {"name": "aircraft_tail_number", "type": "STRING", "mode": "NULLABLE"},
                {"name": "aircraft_operator", "type": "STRING", "mode": "NULLABLE"},
            ],
        },
        {"name": "additional_sanctions_info", "type": "STRING", "mode": "NULLABLE"},
        {"name": "publication_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "ingestion_timestamp", "type": "TIMESTAMP", "mode": "NULLABLE"},
        {"name": "source_url", "type": "STRING", "mode": "NULLABLE"},
    ]
    return {"fields": schema_fields}


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="OFAC SDN Advanced XML → BigQuery Dataflow pipeline"
    )
    parser.add_argument(
        "--gcs_path",
        required=True,
        help="GCS URI of the SDN Advanced XML file (gs://bucket/path/file.xml)",
    )
    parser.add_argument(
        "--bq_table",
        required=True,
        help="BigQuery destination table: project:dataset.table",
    )
    known_args, pipeline_args = parser.parse_known_args(argv)

    pipeline_options = PipelineOptions(pipeline_args)
    pipeline_options.view_as(SetupOptions).save_main_session = True

    logger.info(
        "Starting pipeline: gcs_path=%s, bq_table=%s",
        known_args.gcs_path,
        known_args.bq_table,
    )

    with beam.Pipeline(options=pipeline_options) as p:
        (
            p
            | "CreateGCSPath" >> beam.Create([known_args.gcs_path])
            | "ParseXML" >> beam.ParDo(ParseOfacXmlFn())
            | "CleanRecords" >> beam.ParDo(CleanRecordFn())
            | "WriteToBigQuery"
            >> bigquery.WriteToBigQuery(
                table=known_args.bq_table,
                schema=build_bq_schema(),
                write_disposition=bigquery.BigQueryDisposition.WRITE_TRUNCATE,
                create_disposition=bigquery.BigQueryDisposition.CREATE_IF_NEEDED,
                method=bigquery.WriteToBigQuery.Method.FILE_LOADS,
            )
        )

    logger.info("Pipeline complete.")


if __name__ == "__main__":
    run()
