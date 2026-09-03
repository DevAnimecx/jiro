#!/bin/bash
# Generate Go SDK from OpenAPI spec
# Requires: openapi-generator-cli

echo "Generating Go SDK..."
openapi-generator-cli generate \
    -i openapi-3.1.json \
    -g go \
    -o sdk/go \
    --package-name jiro

echo "Go SDK generated at sdk/go/"
