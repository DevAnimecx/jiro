"""Datasource plugins - specialized data sources for specific domains."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jiro.plugins import BaseDatasourcePlugin, datasource_registry
from jiro.config import Settings
from jiro.scraping.client import ScrapingClient


class SECFilingsPlugin(BaseDatasourcePlugin):
    """SEC EDGAR filings search."""
    
    name = "sec_filings"
    type = "datasource"
    version = "1.0"
    author = "Jiro Team"
    description = "SEC EDGAR database - 10-K, 10-Q, 8-K filings"
    category = "financial"
    homepage = "https://sec.gov"
    
    BASE_URL = "https://data.sec.gov"
    
    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Search SEC filings."""
        # SEC has a public API
        params = {
            "q": query,
            "page": kwargs.get("page", 0),
            "size": kwargs.get("size", 25),
        }
        
        if kwargs.get("form_type"):
            params["formType"] = kwargs["form_type"]
        if kwargs.get("cik"):
            params["cik"] = kwargs["cik"]
        
        url = f"{self.BASE_URL}/submissions"
        
        try:
            resp = await self.client.get(url, params=params)
            data = resp.json()
            return self._parse_results(data)
        except Exception as e:
            raise RuntimeError(f"SEC search failed: {e}")
    
    async def get(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get filing by accession number or CIK."""
        # identifier format: "0001234567-23-000001" or CIK
        if identifier.isdigit():
            # CIK - get latest filings
            url = f"{self.BASE_URL}/submissions/CIK{identifier.zfill(10)}.json"
        else:
            # Accession number
            url = f"{self.BASE_URL}/archives/edgar/data/{identifier}.json"
        
        try:
            resp = await self.client.get(url)
            return resp.json()
        except Exception:
            return None
    
    def _parse_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for filing in data.get("filings", {}).get("recent", []):
            results.append({
                "accession": filing.get("accessionNumber", ""),
                "form_type": filing.get("form", ""),
                "filing_date": filing.get("filingDate", ""),
                "report_date": filing.get("reportDate", ""),
                "description": filing.get("description", ""),
                "size": filing.get("size", 0),
                "source": "sec",
                "type": "filing",
            })
        return results


class ClinicalTrialsPlugin(BaseDatasourcePlugin):
    """ClinicalTrials.gov search."""
    
    name = "clinical_trials"
    type = "datasource"
    version = "1.0"
    author = "Jiro Team"
    description = "ClinicalTrials.gov - clinical studies database"
    category = "medical"
    homepage = "https://clinicaltrials.gov"
    
    BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
    
    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        params = {
            "query.cond": query,
            "pageSize": min(kwargs.get("max_results", 25), 100),
        }
        
        if kwargs.get("phase"):
            params["filter.phase"] = kwargs["phase"]
        if kwargs.get("status"):
            params["filter.overallStatus"] = kwargs["status"]
        if kwargs.get("sponsor"):
            params["query.leadSponsor"] = kwargs["sponsor"]
        
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            data = resp.json()
            return self._parse_results(data)
        except Exception as e:
            raise RuntimeError(f"ClinicalTrials search failed: {e}")
    
    async def get(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get study by NCT ID."""
        url = f"{self.BASE_URL}/{identifier}"
        try:
            resp = await self.client.get(url)
            return resp.json()
        except Exception:
            return None
    
    def _parse_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for study in data.get("studies", []):
            protocol = study.get("protocolSection", {})
            id_module = protocol.get("identificationModule", {})
            status_module = protocol.get("statusModule", {})
            design_module = protocol.get("designModule", {})
            
            results.append({
                "nct_id": id_module.get("nctId", ""),
                "title": id_module.get("officialTitle", ""),
                "status": status_module.get("overallStatus", ""),
                "phase": design_module.get("phaseList", {}).get("phases", []),
                "conditions": protocol.get("conditionsModule", {}).get("conditions", []),
                "interventions": protocol.get("interventionsModule", {}).get("interventions", []),
                "sponsor": protocol.get("sponsorModule", {}).get("leadSponsor", {}).get("name", ""),
                "start_date": status_module.get("startDateStruct", {}).get("date", ""),
                "completion_date": status_module.get("completionDateStruct", {}).get("date", ""),
                "source": "clinicaltrials",
                "type": "study",
            })
        return results


class PatentPlugin(BaseDatasourcePlugin):
    """USPTO Patent search."""
    
    name = "patents"
    type = "datasource"
    version = "1.0"
    author = "Jiro Team"
    description = "USPTO patent database search"
    category = "legal"
    homepage = "https://uspto.gov"
    
    BASE_URL = "https://developer.uspto.gov/ds-api/patents/v1"
    
    def __init__(self, client: ScrapingClient, settings: Settings) -> None:
        super().__init__(client, settings)
        self.api_key = settings.get("plugins.datasource.patents.api_key", "")
    
    async def search(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        params = {
            "q": query,
            "rows": min(kwargs.get("max_results", 25), 100),
            "start": kwargs.get("start", 0),
        }
        
        if kwargs.get("assignee"):
            params["assignee"] = kwargs["assignee"]
        if kwargs.get("inventor"):
            params["inventor"] = kwargs["inventor"]
        if kwargs.get("year_from"):
            params["filingDateFrom"] = f"{kwargs['year_from']}-01-01"
        if kwargs.get("year_to"):
            params["filingDateTo"] = f"{kwargs['year_to']}-12-31"
        
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        
        try:
            resp = await self.client.get(
                f"{self.BASE_URL}/search",
                params=params,
                headers=headers
            )
            data = resp.json()
            return self._parse_results(data)
        except Exception as e:
            raise RuntimeError(f"Patent search failed: {e}")
    
    async def get(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get patent by number."""
        url = f"{self.BASE_URL}/patent/{identifier}"
        try:
            resp = await self.client.get(url)
            return resp.json()
        except Exception:
            return None
    
    def _parse_results(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for patent in data.get("patentList", []):
            results.append({
                "number": patent.get("patentNumber", ""),
                "title": patent.get("inventionTitle", ""),
                "abstract": patent.get("abstractText", "")[:300],
                "assignee": patent.get("assigneeEntityName", ""),
                "inventors": patent.get("inventorNameArray", []),
                "filing_date": patent.get("filingDate", ""),
                "grant_date": patent.get("grantDate", ""),
                "application_number": patent.get("applicationNumber", ""),
                "source": "uspto",
                "type": "patent",
            })
        return results


# Register
datasource_registry.register(SECFilingsPlugin)
datasource_registry.register(ClinicalTrialsPlugin)
datasource_registry.register(PatentPlugin)