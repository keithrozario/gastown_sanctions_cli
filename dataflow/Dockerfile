# Dataflow Flex Template container image
# Uses the Dataflow-provided base image which includes the Python template launcher binary.
# The gcr.io/dataflow-templates-base image includes /opt/google/dataflow/python_template_launcher

FROM gcr.io/dataflow-templates-base/python311-template-launcher-base:latest

ARG WORKDIR=/dataflow/template
RUN mkdir -p ${WORKDIR}
WORKDIR ${WORKDIR}

# Install pipeline dependencies (apache-beam[gcp] is pre-installed in base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pipeline code
COPY pipeline.py .
COPY xml_parser.py .

# Dataflow Flex Template entry point
ENV FLEX_TEMPLATE_PYTHON_PY_FILE="${WORKDIR}/pipeline.py"
