"""Structured output extraction - JSON Schema validation and extraction.

Supports both extractive (zero-LLM-cost) and LLM-powered extraction modes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from jiro.ai.llm import LLM
from jiro.config import Settings
from jiro.log import get_logger
from jiro.search.answer import AnswerSynthesizer

log = get_logger("jiro.search.structured")


@dataclass
class ExtractionResult:
    """Result of structured extraction."""
    data: Dict[str, Any]
    provider: str
    model: Optional[str]
    confidence: float
    mode: str  # "extractive" | "llm"


class SchemaValidator:
    """Validates JSON Schema and extracted data."""
    
    @staticmethod
    def validate_schema(schema: Dict[str, Any]) -> List[str]:
        """Validate JSON Schema structure. Returns list of errors."""
        errors = []
        
        if not isinstance(schema, dict):
            errors.append("Schema must be a JSON object")
            return errors
        
        if "type" not in schema:
            errors.append("Schema must have a 'type' field")
        
        valid_types = ["object", "array", "string", "number", "integer", "boolean", "null"]
        schema_type = schema.get("type")
        if schema_type and schema_type not in valid_types:
            errors.append(f"Invalid type: {schema_type}. Must be one of: {valid_types}")
        
        if schema_type == "object":
            if "properties" not in schema:
                errors.append("Object schema must have 'properties'")
            else:
                for prop_name, prop_schema in schema.get("properties", {}).items():
                    prop_errors = SchemaValidator.validate_schema(prop_schema)
                    errors.extend([f"Property '{prop_name}': {e}" for e in prop_errors])
        
        elif schema_type == "array":
            if "items" not in schema:
                errors.append("Array schema must have 'items'")
            else:
                item_errors = SchemaValidator.validate_schema(schema["items"])
                errors.extend([f"Array item: {e}" for e in item_errors])
        
        return errors
    
    @staticmethod
    def validate_data(data: Any, schema: Dict[str, Any]) -> List[str]:
        """Validate extracted data against schema. Returns list of errors."""
        errors = []
        
        def _validate(value: Any, sch: Dict[str, Any], path: str = "") -> None:
            sch_type = sch.get("type")
            
            if sch_type == "object":
                if not isinstance(value, dict):
                    errors.append(f"{path}: expected object, got {type(value).__name__}")
                    return
                
                required = sch.get("required", [])
                for req in required:
                    if req not in value:
                        errors.append(f"{path}.{req}: required field missing")
                
                properties = sch.get("properties", {})
                for key, val in value.items():
                    if key in properties:
                        _validate(val, properties[key], f"{path}.{key}")
                    elif not sch.get("additionalProperties", True):
                        errors.append(f"{path}.{key}: additional property not allowed")
            
            elif sch_type == "array":
                if not isinstance(value, list):
                    errors.append(f"{path}: expected array, got {type(value).__name__}")
                    return
                
                items_schema = sch.get("items")
                if items_schema:
                    for i, item in enumerate(value):
                        _validate(item, items_schema, f"{path}[{i}]")
            
            elif sch_type == "string":
                if not isinstance(value, str):
                    errors.append(f"{path}: expected string, got {type(value).__name__}")
                else:
                    if "minLength" in sch and len(value) < sch["minLength"]:
                        errors.append(f"{path}: string too short (min {sch['minLength']})")
                    if "maxLength" in sch and len(value) > sch["maxLength"]:
                        errors.append(f"{path}: string too long (max {sch['maxLength']})")
                    if "pattern" in sch:
                        if not re.match(sch["pattern"], value):
                            errors.append(f"{path}: string doesn't match pattern")
            
            elif sch_type in ("number", "integer"):
                if not isinstance(value, (int, float)):
                    errors.append(f"{path}: expected number, got {type(value).__name__}")
                elif sch_type == "integer" and not isinstance(value, int):
                    errors.append(f"{path}: expected integer, got float")
                if "minimum" in sch and value < sch["minimum"]:
                    errors.append(f"{path}: value below minimum ({sch['minimum']})")
                if "maximum" in sch and value > sch["maximum"]:
                    errors.append(f"{path}: value above maximum ({sch['maximum']})")
            
            elif sch_type == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"{path}: expected boolean, got {type(value).__name__}")
        
        _validate(data, schema)
        return errors


class StructuredExtractor:
    """Extracts structured data from text/content using JSON Schema."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = LLM(settings)
        self.answer_synthesizer = AnswerSynthesizer(settings)
        self.validator = SchemaValidator()
        self.mode = settings.get("search.structured.mode", "auto")
    
    async def extract(
        self,
        query: str,
        sources: List[Dict[str, Any]],
        schema: Dict[str, Any],
        mode: Optional[str] = None
    ) -> ExtractionResult:
        """Extract structured data matching the schema from sources."""
        
        # Validate schema
        schema_errors = self.validator.validate_schema(schema)
        if schema_errors:
            raise ValueError(f"Invalid schema: {'; '.join(schema_errors)}")
        
        # Determine extraction mode
        extract_mode = mode or self.mode
        if extract_mode == "auto":
            extract_mode = "llm" if self.llm.available else "extractive"
        
        if extract_mode == "llm" and self.llm.available:
            return await self._extract_llm(query, sources, schema)
        else:
            return await self._extract_extractive(query, sources, schema)
    
    async def _extract_llm(
        self,
        query: str,
        sources: List[Dict[str, Any]],
        schema: Dict[str, Any]
    ) -> ExtractionResult:
        """LLM-powered structured extraction."""
        try:
            # Build context from sources
            context = self._build_context(sources)
            
            # Build schema description for LLM
            schema_desc = self._describe_schema(schema)
            
            system = (
                "You are a precise data extraction system. Extract structured data "
                "from the provided sources matching the given JSON Schema. "
                "Only extract information that is explicitly stated in the sources. "
                "If information is not available, use null or omit optional fields. "
                "Return ONLY valid JSON matching the schema."
            )
            
            user = (
                f"Query: {query}\n\n"
                f"Sources:\n{context}\n\n"
                f"JSON Schema:\n{json.dumps(schema, indent=2)}\n\n"
                f"Extract structured data as JSON:"
            )
            
            response = await self.llm.complete(
                [{"role": "user", "content": user}],
                system=system
            )
            
            # Parse and validate response
            try:
                # Try to extract JSON from response
                extracted = self._extract_json(response)
                
                # Validate against schema
                validation_errors = self.validator.validate_data(extracted, schema)
                if validation_errors:
                    log.warning("LLM extraction validation failed, falling back to extractive", 
                               extra={"errors": validation_errors})
                    return await self._extract_extractive(query, sources, schema)
                
                return ExtractionResult(
                    data=extracted,
                    provider=self.llm.provider_name,
                    model=self.llm.model,
                    confidence=0.9,
                    mode="llm",
                )
            except json.JSONDecodeError:
                log.warning("LLM returned invalid JSON, falling back to extractive")
                return await self._extract_extractive(query, sources, schema)
                
        except Exception as exc:
            log.warning("LLM extraction failed, falling back to extractive", extra={"error": str(exc)})
            return await self._extract_extractive(query, sources, schema)
    
    async def _extract_extractive(
        self,
        query: str,
        sources: List[Dict[str, Any]],
        schema: Dict[str, Any]
    ) -> ExtractionResult:
        """Extractive (zero-LLM-cost) structured extraction."""
        # For extractive mode, we use keyword matching and pattern extraction
        # This is a best-effort approach without LLM
        
        extracted = {}
        schema_type = schema.get("type", "object")
        
        if schema_type == "object":
            properties = schema.get("properties", {})
            for prop_name, prop_schema in properties.items():
                extracted[prop_name] = await self._extract_property(
                    prop_name, prop_schema, query, sources
                )
        
        # Validate
        validation_errors = self.validator.validate_data(extracted, schema)
        confidence = 0.7 if not validation_errors else 0.4
        
        return ExtractionResult(
            data=extracted,
            provider="extractive-fallback",
            model=None,
            confidence=confidence,
            mode="extractive",
        )
    
    async def _extract_property(
        self,
        prop_name: str,
        prop_schema: Dict[str, Any],
        query: str,
        sources: List[Dict[str, Any]]
    ) -> Any:
        """Extract a single property using pattern matching."""
        prop_type = prop_schema.get("type", "string")
        
        # Combine all source text
        all_text = " ".join([
            f"{s.get('title', '')} {s.get('snippet', '')} {s.get('content', '')}"
            for s in sources
        ])
        
        # Try to find relevant information based on property name
        # This is a simple heuristic approach
        patterns = self._get_patterns_for_property(prop_name, prop_schema)
        
        for pattern in patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            if matches:
                if prop_type == "array":
                    return list(set(matches))
                return matches[0]
        
        # Return defaults based on type
        if prop_type == "array":
            return []
        elif prop_type in ("number", "integer"):
            return None
        elif prop_type == "boolean":
            return False
        else:
            return None
    
    def _get_patterns_for_property(self, prop_name: str, prop_schema: Dict[str, Any]) -> List[str]:
        """Generate regex patterns for property extraction."""
        patterns = []
        
        # Common patterns based on property name
        name_lower = prop_name.lower()
        
        if "email" in name_lower:
            patterns.append(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
        elif "phone" in name_lower or "tel" in name_lower:
            patterns.append(r"\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b")
        elif "url" in name_lower or "link" in name_lower or "website" in name_lower:
            patterns.append(r"https?://[^\s]+")
        elif "price" in name_lower or "cost" in name_lower:
            patterns.append(r"\$\s*\d+(?:,\d{3})*(?:\.\d{2})?")
        elif "date" in name_lower:
            patterns.append(r"\b\d{4}-\d{2}-\d{2}\b")
            patterns.append(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
        elif "name" in name_lower:
            patterns.append(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
        
        # Generic pattern for the property name
        patterns.append(rf"{re.escape(prop_name)}[:\s]+([^\n]+)")
        
        return patterns
    
    def _build_context(self, sources: List[Dict[str, Any]]) -> str:
        """Build context string from sources."""
        blocks = []
        for i, src in enumerate(sources, 1):
            snippet = re.sub(r"\s+", " ", src.get("snippet", ""))[:400]
            content = re.sub(r"\s+", " ", src.get("content", ""))[:600]
            blocks.append(
                f"[{i}] {src['title']}\n"
                f"URL: {src['url']}\n"
                f"Snippet: {snippet}\n"
                f"Excerpt: {content}"
            )
        return "\n\n".join(blocks)
    
    def _describe_schema(self, schema: Dict[str, Any]) -> str:
        """Generate human-readable schema description."""
        if schema.get("type") != "object":
            return f"Type: {schema.get('type')}"
        
        props = schema.get("properties", {})
        lines = ["Object with properties:"]
        for name, prop in props.items():
            prop_type = prop.get("type", "any")
            desc = prop.get("description", "")
            required = " (required)" if name in schema.get("required", []) else ""
            lines.append(f"  - {name}: {prop_type}{required} - {desc}")
        return "\n".join(lines)
    
    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response."""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # Try to find JSON in markdown code blocks
        code_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        matches = re.findall(code_block_pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
        
        # Try to find JSON-like structure
        json_pattern = r"(\{.*\})"
        matches = re.findall(json_pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
        
        raise json.JSONDecodeError("Could not extract JSON", text, 0)


async def extract_structured(
    query: str,
    sources: List[Dict[str, Any]],
    schema: Dict[str, Any],
    settings: Settings,
    mode: Optional[str] = None
) -> ExtractionResult:
    """Convenience function for structured extraction."""
    extractor = StructuredExtractor(settings)
    return await extractor.extract(query, sources, schema, mode)