#!/bin/bash
# Generate TypeScript SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating TypeScript SDK..."
openapi-generator-cli generate \
    -i openapi-3.1.json \
    -g typescript-fetch \
    -o sdk/typescript \
    --additional-properties=npmName=@jiro/client,npmVersion=0.2.0

echo "TypeScript SDK generated at sdk/typescript/"
