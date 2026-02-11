#!/usr/bin/env python3
"""
Railway GraphQL client for updating service-level environment variables.
"""

from typing import Optional, Tuple

import requests

RAILWAY_GRAPHQL_URL = "https://backboard.railway.app/graphql/v2"


class RailwayVariableClient:
    """Minimal Railway API client for variable upsert operations."""

    def __init__(
        self,
        api_token: str,
        project_id: str,
        environment_id: str,
        service_id: str,
        timeout: int = 10,
    ):
        self._api_token = api_token
        self._project_id = project_id
        self._environment_id = environment_id
        self._service_id = service_id
        self._timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

    def upsert_service_variable(self, name: str, value: str) -> Tuple[bool, Optional[str]]:
        """
        Upsert one service variable in Railway.

        Returns:
            (True, None) on success, otherwise (False, error_message)
        """
        mutation = """
        mutation VariableUpsert($input: VariableUpsertInput!) {
          variableUpsert(input: $input)
        }
        """

        payload = {
            "query": mutation,
            "variables": {
                "input": {
                    "projectId": self._project_id,
                    "environmentId": self._environment_id,
                    "serviceId": self._service_id,
                    "name": name,
                    "value": value,
                }
            },
        }

        try:
            response = requests.post(
                RAILWAY_GRAPHQL_URL,
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except Exception as exc:
            return False, f"Railway variable upsert request failed: {exc}"

        if response.status_code != 200:
            return False, f"Railway API error {response.status_code}: {response.text}"

        try:
            body = response.json()
        except ValueError:
            return False, f"Railway API returned non-JSON response: {response.text}"

        errors = body.get("errors") or []
        if errors:
            joined = "; ".join(
                f"{err.get('message', str(err))} (path={err.get('path')})"
                for err in errors
            )
            return False, f"Railway GraphQL error: {joined}"

        upsert_result = body.get("data", {}).get("variableUpsert")
        if upsert_result is None:
            return False, f"Unexpected Railway response shape: {body}"
        if isinstance(upsert_result, bool) and not upsert_result:
            return False, "Railway variable upsert returned false"

        return True, None
