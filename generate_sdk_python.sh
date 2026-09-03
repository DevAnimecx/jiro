#!/bin/bash
# Generate Python SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating Python SDK..."
openapi-generator-cli generate \
    -i openapi-3.1.json \
    -g python \
    -o sdk/python \
    --package-name jiro_client \
    --additional-properties=packageVersion=0.2.0

echo "Python SDK generated at sdk/python/"
