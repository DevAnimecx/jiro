"""Search history tracking and querying.

Provides:
- Track all search queries with timestamps
- Query past searches by time range, query text, or engine
- Search history analytics
- History export and cleanup
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SearchEntry:
    """A single search history entry."""
    entry_id: str
    query: str
    engine: str
    timestamp: float
    result_count: int = 0
    duration_ms: float = 0
    cached: bool = False
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "query": self.query,
            "engine": self.engine,
            "timestamp": self.timestamp,
            "result_count": self.result_count,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
            "user_id": self.user_id,
            "metadata": self.metadata,
        }


class SearchHistory:
    """Track and query search history."""

    def __init__(self, max_entries: int = 10000) -> None:
        self.max_entries = max_entries
        self._entries: List[SearchEntry] = []
        self._query_index: Dict[str, List[int]] = {}
        self._engine_index: Dict[str, List[int]] = {}

    def record(
        self,
        query: str,
        engine: str = "google",
        result_count: int = 0,
        duration_ms: float = 0,
        cached: bool = False,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SearchEntry:
        """Record a search."""
        entry = SearchEntry(
            entry_id=str(uuid.uuid4())[:8],
            query=query,
            engine=engine,
            timestamp=time.time(),
            result_count=result_count,
            duration_ms=duration_ms,
            cached=cached,
            user_id=user_id,
            metadata=metadata or {},
        )
        
        idx = len(self._entries)
        self._entries.append(entry)
        
        # Update indexes
        query_lower = query.lower()
        if query_lower not in self._query_index:
            self._query_index[query_lower] = []
        self._query_index[query_lower].append(idx)
        
        if engine not in self._engine_index:
            self._engine_index[engine] = []
        self._engine_index[engine].append(idx)
        
        # Trim if over max
        if len(self._entries) > self.max_entries:
            self._trim()
        
        return entry

    def query(
        self,
        search_text: Optional[str] = None,
        engine: Optional[str] = None,
        user_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
    ) -> List[SearchEntry]:
        """Query search history."""
        entries = self._entries.copy()
        
        if search_text:
            search_lower = search_text.lower()
            entries = [
                e for e in entries
                if search_lower in e.query.lower()
            ]
        
        if engine:
            entries = [e for e in entries if e.engine == engine]
        
        if user_id:
            entries = [e for e in entries if e.user_id == user_id]
        
        if start_time:
            entries = [e for e in entries if e.timestamp >= start_time]
        
        if end_time:
            entries = [e for e in entries if e.timestamp <= end_time]
        
        # Sort by timestamp descending
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        return entries[:limit]

    def get_recent(self, limit: int = 10) -> List[SearchEntry]:
        """Get recent searches."""
        return self._entries[-limit:][::-1]

    def get_popular_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get most popular queries."""
        query_counts: Dict[str, int] = {}
        for entry in self._entries:
            query_lower = entry.query.lower()
            query_counts[query_lower] = query_counts.get(query_lower, 0) + 1
        
        sorted_queries = sorted(query_counts.items(), key=lambda x: x[1], reverse=True)
        
        return [
            {"query": q, "count": c}
            for q, c in sorted_queries[:limit]
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get search history statistics."""
        if not self._entries:
            return {
                "total_searches": 0,
                "unique_queries": 0,
                "engines_used": 0,
                "avg_results": 0,
                "avg_duration_ms": 0,
                "cache_hit_rate": 0,
            }
        
        total = len(self._entries)
        unique_queries = len(set(e.query.lower() for e in self._entries))
        engines_used = len(set(e.engine for e in self._entries))
        avg_results = sum(e.result_count for e in self._entries) / total
        avg_duration = sum(e.duration_ms for e in self._entries) / total
        cache_hits = sum(1 for e in self._entries if e.cached)
        
        return {
            "total_searches": total,
            "unique_queries": unique_queries,
            "engines_used": engines_used,
            "avg_results": round(avg_results, 1),
            "avg_duration_ms": round(avg_duration, 1),
            "cache_hit_rate": f"{(cache_hits / total * 100):.1f}%",
        }

    def get_time_distribution(self, hours: int = 24) -> Dict[int, int]:
        """Get search distribution by hour of day."""
        now = time.time()
        cutoff = now - (hours * 3600)
        
        hourly_counts: Dict[int, int] = {h: 0 for h in range(24)}
        
        for entry in self._entries:
            if entry.timestamp >= cutoff:
                hour = int((entry.timestamp % 86400) / 3600)
                hourly_counts[hour] += 1
        
        return hourly_counts

    def export_history(
        self,
        format: str = "json",
        limit: Optional[int] = None,
    ) -> str:
        """Export search history."""
        entries = self._entries.copy()
        if limit:
            entries = entries[-limit:]
        
        data = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(entries),
            "entries": [e.to_dict() for e in entries],
        }
        
        if format == "json":
            import json
            return json.dumps(data, indent=2, ensure_ascii=False)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            if entries:
                writer = csv.DictWriter(output, fieldnames=entries[0].to_dict().keys())
                writer.writeheader()
                for entry in entries:
                    writer.writerow(entry.to_dict())
            return output.getvalue()
        else:
            raise ValueError(f"Unsupported format: {format}")

    def clear(self, older_than_days: Optional[int] = None) -> int:
        """Clear search history."""
        if older_than_days is None:
            count = len(self._entries)
            self._entries.clear()
            self._query_index.clear()
            self._engine_index.clear()
            return count
        
        cutoff = time.time() - (older_than_days * 86400)
        original_count = len(self._entries)
        self._entries = [e for e in self._entries if e.timestamp < cutoff]
        
        # Rebuild indexes
        self._query_index.clear()
        self._engine_index.clear()
        for idx, entry in enumerate(self._entries):
            query_lower = entry.query.lower()
            if query_lower not in self._query_index:
                self._query_index[query_lower] = []
            self._query_index[query_lower].append(idx)
            
            if entry.engine not in self._engine_index:
                self._engine_index[entry.engine] = []
            self._engine_index[entry.engine].append(idx)
        
        return original_count - len(self._entries)

    def _trim(self) -> None:
        """Trim history to max entries."""
        trim_count = len(self._entries) - self.max_entries
        if trim_count > 0:
            self._entries = self._entries[trim_count:]
            # Rebuild indexes
            self._query_index.clear()
            self._engine_index.clear()
            for idx, entry in enumerate(self._entries):
                query_lower = entry.query.lower()
                if query_lower not in self._query_index:
                    self._query_index[query_lower] = []
                self._query_index[query_lower].append(idx)
                
                if entry.engine not in self._engine_index:
                    self._engine_index[entry.engine] = []
                self._engine_index[entry.engine].append(idx)


# Global instance
_search_history = SearchHistory()


def get_search_history() -> SearchHistory:
    """Get the global search history."""
    return _search_history
