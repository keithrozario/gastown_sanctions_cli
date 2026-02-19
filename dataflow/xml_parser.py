"""
OFAC SDN Advanced XML Parser

Parses the OFAC Specially Designated Nationals (SDN) Advanced XML format
into flat dictionaries suitable for BigQuery ingestion.

The Advanced XML is a highly normalized format with cross-references between:
  - ReferenceValueSets   (enumeration lookups by numeric ID)
  - Locations            (geographic data — city, address, country)
  - IDRegDocuments       (identity documents — passports, national IDs)
  - DistinctParties      (the sanctioned entities themselves)
  - SanctionsEntries     (what programs/legal authorities apply to each entity)

This parser performs a two-pass approach:
  Pass 1: Build lookup maps for all reference data
  Pass 2: Iterate DistinctParties and emit BigQuery row dicts
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

OFAC_SOURCE_URL = (
    "https://sanctionslistservice.ofac.treas.gov"
    "/api/PublicationPreview/exports/SDN_ADVANCED.XML"
)

# ── Name-part ordering for constructing full_name ────────────────────────────
NAME_PART_ORDER = {
    "last name": 0,
    "last": 0,
    "entity name": 0,
    "vessel name": 0,
    "aircraft name": 0,
    "first name": 1,
    "first": 1,
    "middle name": 2,
    "middle": 2,
    "patronymic": 3,
    "matronymic": 4,
}

# ── Feature type name fragments → field mapping ───────────────────────────────
VESSEL_FEATURES = {
    "vessel call sign": "vessel_call_sign",
    "vessel type": "vessel_type",
    "vessel tonnage": "vessel_tonnage",
    "gross registered tonnage": "vessel_grt",
    "vessel flag": "vessel_flag",
    "vessel owner": "vessel_owner",
    "mmsi": "vessel_mmsi",
    "imo": "vessel_imo",
}

AIRCRAFT_FEATURES = {
    "aircraft construction number": "aircraft_serial",
    "aircraft manufacturer's serial number": "aircraft_serial",
    "aircraft model": "aircraft_type",
    "aircraft operator": "aircraft_operator",
    "aircraft tail number": "aircraft_tail_number",
    "aircraft type": "aircraft_type",
    "aircraft manufacturer": "aircraft_manufacturer",
}


def _local_tag(element_tag: str) -> str:
    """Strip XML namespace prefix, returning only the local tag name."""
    return element_tag.split("}")[-1] if "}" in element_tag else element_tag


def _iter_tag(parent, local_name: str):
    """Iterate direct children with a given local tag name."""
    for child in parent:
        if _local_tag(child.tag) == local_name:
            yield child


def _find_tag(parent, local_name: str):
    """Return first direct child with a given local tag name, or None."""
    for child in parent:
        if _local_tag(child.tag) == local_name:
            return child
    return None


def _parse_date_period(dp_elem) -> str | None:
    """
    Extract a human-readable date string from a DatePeriod XML element.

    Returns the earliest known date in YYYY, YYYY-MM, or YYYY-MM-DD format.
    Returns None if no parseable date found.
    """
    for boundary in dp_elem:
        boundary_tag = _local_tag(boundary.tag)
        if boundary_tag not in ("Start", "End"):
            continue
        from_elem = _find_tag(boundary, "From")
        if from_elem is None:
            continue
        parts = {}
        for d in from_elem:
            parts[_local_tag(d.tag)] = (d.text or "").strip()
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


def _build_ref_maps(root) -> dict:
    """
    Pass 1a: Parse ReferenceValueSets into lookup dicts.

    Returns a dict of dicts:
      refs["AliasTypeValues"]        = {id: text}
      refs["PartySubTypeValues"]     = {id: text}
      refs["FeatureTypeValues"]      = {id: text}
      refs["ScriptValues"]           = {id: text}
      refs["NamePartTypeValues"]     = {id: text}
      refs["CountryValues"]          = {id: text}
      refs["LocPartTypeValues"]      = {id: text}
      refs["IDRegDocTypeValues"]     = {id: text}
      refs["SanctionsProgramValues"] = {id: text}
      refs["LegalBasisValues"]       = {id: short_ref_text}
    """
    refs = {}

    for child in root:
        if _local_tag(child.tag) != "ReferenceValueSets":
            continue

        for set_elem in child:
            set_name = _local_tag(set_elem.tag)
            mapping = {}

            if set_name == "LegalBasisValues":
                # Nested: <LegalBasis ID="X"><LegalBasisShortRef>text</LegalBasisShortRef>
                for lb in set_elem:
                    lb_id = lb.get("ID")
                    if not lb_id:
                        continue
                    short_ref = _find_tag(lb, "LegalBasisShortRef")
                    mapping[lb_id] = (short_ref.text or "").strip() if short_ref else ""
            else:
                # Simple: <ElementType ID="X">text</ElementType>
                for item in set_elem:
                    item_id = item.get("ID")
                    if item_id:
                        mapping[item_id] = (item.text or "").strip()

            refs[set_name] = mapping
        break  # Only one ReferenceValueSets block

    return refs


def _build_locations_map(root, country_values: dict, loc_part_types: dict) -> dict:
    """
    Pass 1b: Parse Locations section into a lookup dict.

    Returns: {location_id: {address, city, state_province, postal_code, country, region}}
    """
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

            loc_data = {
                "address": "",
                "city": "",
                "state_province": "",
                "postal_code": "",
                "country": "",
                "region": "",
            }

            for lchild in loc:
                lt = _local_tag(lchild.tag)

                if lt == "LocationCountry":
                    country_id = lchild.get("CountryID")
                    loc_data["country"] = country_values.get(country_id, "")

                elif lt == "LocationAreaCode":
                    # AreaCode is a broader geographic region (province/state/area)
                    # We don't have a separate field for it; skip silently
                    pass

                elif lt == "LocationPart":
                    loc_part_type_id = lchild.get("LocPartTypeID")
                    loc_part_name = loc_part_types.get(loc_part_type_id, "").lower()
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
                        # Fallback: append to address
                        if loc_data["address"]:
                            loc_data["address"] += f", {part_value}"
                        else:
                            loc_data["address"] = part_value

            locations[loc_id] = loc_data
        break

    return locations


def _build_id_docs_map(root, country_values: dict, id_reg_doc_types: dict) -> dict:
    """
    Pass 1c: Parse IDRegDocuments into a lookup dict.

    Returns: {doc_id: {id_type, id_number, country, issue_date, expiry_date, is_fraudulent}}
    """
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

            # Type may be on the element as attribute or as child element
            doc_type_id = doc.get("IDRegDocTypeID", "")
            doc_data = {
                "id_type": id_reg_doc_types.get(doc_type_id, ""),
                "id_number": "",
                "country": "",
                "issue_date": "",
                "expiry_date": "",
                "is_fraudulent": False,
            }

            for dchild in doc:
                dct = _local_tag(dchild.tag)

                if dct == "IDRegDocType":
                    type_id = dchild.get("IDRegDocTypeID")
                    doc_data["id_type"] = id_reg_doc_types.get(
                        type_id, (dchild.text or "").strip()
                    )
                elif dct == "IDRegDocumentID":
                    doc_data["id_number"] = (dchild.text or "").strip()
                elif dct == "IssuingCountry":
                    country_id = dchild.get("CountryID")
                    doc_data["country"] = country_values.get(country_id, "")
                elif dct == "IDRegDocDateOfIssuance":
                    doc_data["issue_date"] = _parse_date_period(dchild) or ""
                elif dct == "IDRegDocExpirationDate":
                    doc_data["expiry_date"] = _parse_date_period(dchild) or ""

            docs[doc_id] = doc_data
        break

    return docs


def _build_sanctions_map(root, legal_basis_map: dict, sanctions_programs: dict) -> dict:
    """
    Pass 1d: Parse SanctionsEntries to map profile_id → sanctions data.

    Returns: {profile_id: {programs: [...], legal_authorities: [...], remarks: str}}
    """
    sanctions = {}

    for child in root:
        if _local_tag(child.tag) != "SanctionsEntries":
            continue

        for entry in child:
            if _local_tag(entry.tag) != "SanctionsEntry":
                continue

            profile_id = None
            programs = []
            legal_authorities = []
            remarks = ""

            for echild in entry:
                ect = _local_tag(echild.tag)

                if ect == "ProfileID":
                    profile_id = (echild.text or "").strip()

                elif ect == "SanctionsMeasure":
                    prog_id = echild.get("SanctionsProgramID", "")
                    if prog_id:
                        prog_name = sanctions_programs.get(prog_id, prog_id)
                        if prog_name and prog_name not in programs:
                            programs.append(prog_name)

                    for sm_child in echild:
                        smct = _local_tag(sm_child.tag)
                        if smct == "LegalAuthority":
                            # Attribute-based lookup
                            lb_id = sm_child.get("LegalBasisID", "")
                            if lb_id and lb_id in legal_basis_map:
                                la = legal_basis_map[lb_id]
                                if la and la not in legal_authorities:
                                    legal_authorities.append(la)
                            # Also check inline child element
                            for la_child in sm_child:
                                if _local_tag(la_child.tag) == "LegalBasisShortRef":
                                    la_text = (la_child.text or "").strip()
                                    if la_text and la_text not in legal_authorities:
                                        legal_authorities.append(la_text)
                        elif smct == "SanctionsList":
                            prog = (sm_child.text or "").strip()
                            if prog and prog not in programs:
                                programs.append(prog)

                elif ect == "EntryEvent":
                    # EntryEvent may also carry legal authority
                    for ev_child in echild:
                        if _local_tag(ev_child.tag) == "LegalAuthority":
                            lb_id = ev_child.get("LegalBasisID", "")
                            if lb_id and lb_id in legal_basis_map:
                                la = legal_basis_map[lb_id]
                                if la and la not in legal_authorities:
                                    legal_authorities.append(la)
                            for la_child in ev_child:
                                if _local_tag(la_child.tag) == "LegalBasisShortRef":
                                    la_text = (la_child.text or "").strip()
                                    if la_text and la_text not in legal_authorities:
                                        legal_authorities.append(la_text)

                elif ect == "SanctionsList":
                    # Top-level SanctionsList on the entry (alternative location)
                    prog = (echild.text or "").strip()
                    if prog and prog not in programs:
                        programs.append(prog)

                elif ect == "Remarks":
                    remarks = (echild.text or "").strip()

            if profile_id:
                if profile_id not in sanctions:
                    sanctions[profile_id] = {
                        "programs": [],
                        "legal_authorities": [],
                        "remarks": "",
                    }
                se = sanctions[profile_id]
                se["programs"].extend(p for p in programs if p not in se["programs"])
                se["legal_authorities"].extend(
                    la for la in legal_authorities if la not in se["legal_authorities"]
                )
                if remarks:
                    se["remarks"] = remarks
        break

    return sanctions


def _parse_identity(identity_elem, record: dict, alias_types: dict,
                    script_values: dict, name_part_types: dict):
    """
    Parse an Identity element and populate primary_name and aliases in record.

    NamePartGroups within Identity define the type of each name part group:
      NamePartGroup.ID → referenced by NamePartValue.NamePartGroupID
      NamePartGroup.NamePartTypeID → maps to name_part_types (Last Name, First Name, etc.)
    """
    # Build name-part-group lookup (scoped to this Identity)
    npg_map = {}  # group_id -> type_name
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

    # Parse each Alias
    for alias in _iter_tag(identity_elem, "Alias"):
        alias_type_id = alias.get("AliasTypeID", "")
        is_primary = alias.get("Primary", "false").strip().lower() == "true"
        is_low_quality = alias.get("LowQuality", "false").strip().lower() == "true"

        alias_type = alias_types.get(alias_type_id, "a.k.a.")
        alias_quality = "weak" if is_low_quality else "strong"

        for doc_name in _iter_tag(alias, "DocumentedName"):
            parts_raw = []  # (sort_key, part_type, script, value)

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
            name_parts_bq = [
                {"part_type": pt, "part_value": pv, "script": sc}
                for _, pt, sc, pv in parts_raw
            ]
            full_name = " ".join(pv for _, _, _, pv in parts_raw if pv)

            if not full_name:
                continue

            if is_primary and record["primary_name"] is None:
                record["primary_name"] = {
                    "full_name": full_name,
                    "name_parts": name_parts_bq,
                }
            else:
                record["aliases"].append({
                    "alias_type": alias_type,
                    "alias_quality": alias_quality,
                    "full_name": full_name,
                    "name_parts": name_parts_bq,
                })


def _parse_features(profile_elem, record: dict, feature_types: dict,
                    country_values: dict, locations_map: dict, id_docs_map: dict):
    """
    Parse Feature elements from a Profile and populate record fields.

    Feature types of interest:
      Nationality, Citizenship, Date of Birth, Place of Birth,
      Gender, Title, vessel/aircraft fields, Additional Sanctions Information,
      and address-linked location features.
    """
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
                    if date_val:
                        if "birth" in ft_name and "date" in ft_name:
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

                    # Location references (addresses, place of birth)
                    for vdc in fvc:
                        vdct = _local_tag(vdc.tag)
                        if vdct == "LocationID":
                            loc_id = (vdc.text or "").strip()
                            if loc_id and loc_id in locations_map:
                                loc = locations_map[loc_id]
                                _apply_location(
                                    loc, ft_name, record
                                )
                        elif vdct == "IDRegDocumentReference":
                            doc_id = vdc.get("DocumentID", "")
                            if doc_id and doc_id in id_docs_map:
                                doc = dict(id_docs_map[doc_id])
                                record["id_documents"].append(doc)

                elif fvct == "VersionLocation":
                    loc_id = fvc.get("LocationID", "")
                    if loc_id and loc_id in locations_map:
                        _apply_location(locations_map[loc_id], ft_name, record)

            # Post-comment analysis for specific feature types
            if "gender" in ft_name and comment:
                record["gender"] = comment
            elif "title" in ft_name and comment:
                record["title"] = comment
            elif "additional sanctions" in ft_name and comment:
                additional_sanctions.append(comment)

            # Vessel feature extraction from comment
            for vessel_key, field_name in VESSEL_FEATURES.items():
                if vessel_key in ft_name and comment:
                    vessel[field_name] = comment
                    break

            # Aircraft feature extraction from comment
            for aircraft_key, field_name in AIRCRAFT_FEATURES.items():
                if aircraft_key in ft_name and comment:
                    aircraft[field_name] = comment
                    break

    if vessel:
        record["vessel_info"] = {
            "vessel_type": vessel.get("vessel_type"),
            "vessel_flag": vessel.get("vessel_flag"),
            "vessel_owner": vessel.get("vessel_owner"),
            "vessel_tonnage": vessel.get("vessel_tonnage"),
            "vessel_grt": vessel.get("vessel_grt"),
            "vessel_call_sign": vessel.get("vessel_call_sign"),
            "vessel_mmsi": vessel.get("vessel_mmsi"),
            "vessel_imo": vessel.get("vessel_imo"),
        }

    if aircraft:
        record["aircraft_info"] = {
            "aircraft_type": aircraft.get("aircraft_type"),
            "aircraft_manufacturer": aircraft.get("aircraft_manufacturer"),
            "aircraft_serial": aircraft.get("aircraft_serial"),
            "aircraft_tail_number": aircraft.get("aircraft_tail_number"),
            "aircraft_operator": aircraft.get("aircraft_operator"),
        }

    if additional_sanctions:
        record["additional_sanctions_info"] = "; ".join(additional_sanctions)


def _apply_location(loc: dict, ft_name: str, record: dict):
    """Apply a resolved location to the appropriate record field."""
    if "birth" in ft_name and "place" in ft_name:
        pob = ", ".join(
            filter(None, [loc.get("city", ""), loc.get("state_province", ""),
                          loc.get("country", "")])
        )
        if pob and pob not in record["places_of_birth"]:
            record["places_of_birth"].append(pob)
    else:
        addr_entry = {
            "address": loc.get("address", ""),
            "city": loc.get("city", ""),
            "state_province": loc.get("state_province", ""),
            "postal_code": loc.get("postal_code", ""),
            "country": loc.get("country", ""),
            "region": loc.get("region", ""),
        }
        if any(addr_entry.values()):
            record["addresses"].append(addr_entry)


def parse_ofac_advanced_xml(xml_bytes: bytes) -> tuple[str | None, list[dict]]:
    """
    Parse the full OFAC SDN Advanced XML document.

    Args:
        xml_bytes: Raw XML bytes from the OFAC download

    Returns:
        (publication_date, records) where:
          - publication_date: ISO date string from XML DateOfIssue (or None)
          - records: list of BigQuery row dicts, one per DistinctParty
    """
    logger.info("Parsing OFAC Advanced XML (%d bytes)", len(xml_bytes))

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"Failed to parse XML: {exc}") from exc

    # ── Pass 1: Build all lookup maps ─────────────────────────────────────────

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

    logger.info(
        "Reference maps: %d alias types, %d countries, %d feature types, "
        "%d name part types, %d legal bases",
        len(alias_types), len(country_values), len(feature_types),
        len(name_part_types), len(legal_basis_map),
    )

    locations_map = _build_locations_map(root, country_values, loc_part_types)
    id_docs_map = _build_id_docs_map(root, country_values, id_reg_doc_types)
    sanctions_map = _build_sanctions_map(root, legal_basis_map, sanctions_programs)

    logger.info(
        "Lookup maps: %d locations, %d id_docs, %d sanctions entries",
        len(locations_map), len(id_docs_map), len(sanctions_map),
    )

    # ── Pass 2: Parse DistinctParties ─────────────────────────────────────────

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
                logger.warning("DistinctParty without FixedRef — skipping")
                continue

            record = {
                "sdn_entry_id": int(fixed_ref),
                "sdn_type": None,
                "programs": [],
                "legal_authorities": [],
                "primary_name": None,
                "aliases": [],
                "addresses": [],
                "id_documents": [],
                "dates_of_birth": [],
                "places_of_birth": [],
                "nationalities": [],
                "citizenships": [],
                "title": None,
                "gender": None,
                "remarks": None,
                "vessel_info": None,
                "aircraft_info": None,
                "additional_sanctions_info": None,
                "publication_date": pub_date,
                "ingestion_timestamp": ingestion_ts,
                "source_url": OFAC_SOURCE_URL,
            }

            for dp_child in dp:
                dp_ct = _local_tag(dp_child.tag)

                if dp_ct == "Profile":
                    profile_id = dp_child.get("ID", "")
                    party_subtype_id = dp_child.get("PartySubTypeID", "")
                    record["sdn_type"] = party_subtypes.get(
                        party_subtype_id, party_subtype_id or None
                    )

                    # Inject sanctions data (programs, legal authorities, remarks)
                    if profile_id in sanctions_map:
                        se = sanctions_map[profile_id]
                        record["programs"] = list(se.get("programs", []))
                        record["legal_authorities"] = list(se.get("legal_authorities", []))
                        record["remarks"] = se.get("remarks") or None

                    for pchild in dp_child:
                        pct = _local_tag(pchild.tag)
                        if pct == "Identity":
                            _parse_identity(
                                pchild, record, alias_types,
                                script_values, name_part_types,
                            )
                        elif pct == "Feature":
                            # Feature is on Profile, not Identity
                            pass  # handled below via profile_elem

                    # Parse features — pass the full Profile element
                    _parse_features(
                        dp_child, record, feature_types, country_values,
                        locations_map, id_docs_map,
                    )

            records.append(record)
        break  # Only one DistinctParties block

    logger.info("Parsed %d DistinctParty records", len(records))
    return pub_date, records
