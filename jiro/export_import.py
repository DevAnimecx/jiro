"""Export and import functionality for search results and configurations.

Provides:
- Export search results to JSON, CSV, Markdown
- Import configurations from files
- Backup and restore system settings
- Data migration utilities
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class ExportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"
    YAML = "yaml"


@dataclass
class ExportOptions:
    """Options for exporting data."""
    format: ExportFormat = ExportFormat.JSON
    include_metadata: bool = True
    pretty_print: bool = True
    max_results: Optional[int] = None
    fields: Optional[List[str]] = None


class SearchExporter:
    """Export search results to various formats."""

    def export_results(
        self,
        results: List[Dict[str, Any]],
        options: Optional[ExportOptions] = None,
    ) -> str:
        """Export search results to string."""
        options = options or ExportOptions()
        
        if options.max_results:
            results = results[:options.max_results]
        
        if options.fields:
            results = [
                {k: v for k, v in r.items() if k in options.fields}
                for r in results
            ]

        if options.format == ExportFormat.JSON:
            return self._export_json(results, options)
        elif options.format == ExportFormat.CSV:
            return self._export_csv(results)
        elif options.format == ExportFormat.MARKDOWN:
            return self._export_markdown(results)
        elif options.format == ExportFormat.YAML:
            return self._export_yaml(results)
        else:
            raise ValueError(f"Unsupported format: {options.format}")

    def export_to_file(
        self,
        results: List[Dict[str, Any]],
        filepath: str,
        options: Optional[ExportOptions] = None,
    ) -> str:
        """Export results to a file."""
        options = options or ExportOptions()
        content = self.export_results(results, options)
        
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        
        return str(path)

    def _export_json(self, results: List[Dict[str, Any]], options: ExportOptions) -> str:
        """Export as JSON."""
        data = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(results),
            "results": results,
        }
        
        if options.pretty_print:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return json.dumps(data, ensure_ascii=False)

    def _export_csv(self, results: List[Dict[str, Any]]) -> str:
        """Export as CSV."""
        if not results:
            return ""
        
        output = io.StringIO()
        fieldnames = list(results[0].keys())
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
        
        return output.getvalue()

    def _export_markdown(self, results: List[Dict[str, Any]]) -> str:
        """Export as Markdown table."""
        if not results:
            return "No results to export."
        
        lines = [
            f"# Search Results ({len(results)} items)",
            "",
        ]
        
        # Table header
        headers = list(results[0].keys())
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # Table rows
        for result in results:
            row = [str(result.get(h, ""))[:50] for h in headers]
            lines.append("| " + " | ".join(row) + " |")
        
        return "\n".join(lines)

    def _export_yaml(self, results: List[Dict[str, Any]]) -> str:
        """Export as YAML."""
        try:
            import yaml
            data = {
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "count": len(results),
                "results": results,
            }
            return yaml.dump(data, default_flow_style=False, allow_unicode=True)
        except ImportError:
            # Fallback to simple YAML-like format
            lines = ["exported_at: " + time.strftime("%Y-%m-%dT%H:%M:%SZ"), f"count: {len(results)}", "results:"]
            for r in results:
                lines.append("  -")
                for k, v in r.items():
                    lines.append(f"    {k}: {v}")
            return "\n".join(lines)


class ConfigExporter:
    """Export and import configurations."""

    def export_config(
        self,
        config: Dict[str, Any],
        filepath: Optional[str] = None,
        format: ExportFormat = ExportFormat.JSON,
    ) -> str:
        """Export configuration to string or file."""
        data = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "config": config,
        }
        
        if format == ExportFormat.JSON:
            content = json.dumps(data, indent=2, ensure_ascii=False)
        elif format == ExportFormat.YAML:
            try:
                import yaml
                content = yaml.dump(data, default_flow_style=False, allow_unicode=True)
            except ImportError:
                content = json.dumps(data, indent=2, ensure_ascii=False)
        else:
            raise ValueError(f"Unsupported config format: {format}")
        
        if filepath:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return str(path)
        
        return content

    def import_config(
        self,
        source: Union[str, Path],
    ) -> Dict[str, Any]:
        """Import configuration from file."""
        path = Path(source)
        
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        
        content = path.read_text(encoding="utf-8")
        
        if path.suffix in (".json",):
            data = json.loads(content)
        elif path.suffix in (".yaml", ".yml"):
            try:
                import yaml
                data = yaml.safe_load(content)
            except ImportError:
                raise ImportError("PyYAML is required to import YAML configs")
        else:
            # Try JSON first, then YAML
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                try:
                    import yaml
                    data = yaml.safe_load(content)
                except ImportError:
                    raise ValueError(f"Unsupported config format: {path.suffix}")
        
        return data.get("config", data)

    def merge_configs(
        self,
        base: Dict[str, Any],
        override: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge two configurations with override taking precedence."""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self.merge_configs(result[key], value)
            else:
                result[key] = value
        
        return result


class BackupManager:
    """Backup and restore system state."""

    def __init__(self, backup_dir: str = "~/.jiro/backups") -> None:
        self.backup_dir = Path(backup_dir).expanduser()

    def create_backup(
        self,
        data: Dict[str, Any],
        name: Optional[str] = None,
    ) -> str:
        """Create a backup."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_name = name or f"backup_{timestamp}"
        filepath = self.backup_dir / f"{backup_name}.json"
        
        backup_data = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "name": backup_name,
            "data": data,
        }
        
        filepath.write_text(json.dumps(backup_data, indent=2), encoding="utf-8")
        return str(filepath)

    def list_backups(self) -> List[Dict[str, Any]]:
        """List available backups."""
        if not self.backup_dir.exists():
            return []
        
        backups = []
        for filepath in sorted(self.backup_dir.glob("*.json"), reverse=True):
            try:
                content = filepath.read_text(encoding="utf-8")
                data = json.loads(content)
                backups.append({
                    "name": data.get("name", filepath.stem),
                    "created_at": data.get("created_at", ""),
                    "filepath": str(filepath),
                    "size": filepath.stat().st_size,
                })
            except Exception:
                continue
        
        return backups

    def restore_backup(self, filepath: str) -> Dict[str, Any]:
        """Restore a backup."""
        path = Path(filepath)
        
        if not path.exists():
            raise FileNotFoundError(f"Backup file not found: {path}")
        
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        
        return data.get("data", data)


# Global instances
_search_exporter = SearchExporter()
_config_exporter = ConfigExporter()
_backup_manager = BackupManager()


def get_search_exporter() -> SearchExporter:
    """Get the global search exporter."""
    return _search_exporter


def get_config_exporter() -> ConfigExporter:
    """Get the global config exporter."""
    return _config_exporter


def get_backup_manager() -> BackupManager:
    """Get the global backup manager."""
    return _backup_manager
