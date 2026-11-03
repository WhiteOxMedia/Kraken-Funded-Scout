"""Utility to expand environment variables in st.secrets using singleton."""
import os
import streamlit as st
from streamlit.runtime.secrets import secrets_singleton

import httpx


def _expand_value(value, key=None):
    """Recursively expand environment variables in values.

    For $VAR_NAME patterns, checks:
      1. OS environment variables
      2. OS keyring (service 'workshop', username = VAR_NAME)
    """
    if isinstance(value, str):
        if value.startswith('$'):
            var_name = value[1:]
            
            # 1. Try environment variable first
            env_value = os.getenv(var_name)

            # 2. Fall back to OS keyring
            if not env_value:
                try:
                    import keyring
                    env_value = keyring.get_password("workshop", var_name)
                except Exception:
                    pass
            
            if not env_value:
                return None

            # Special handling for GCP private_key - convert literal \n to actual newlines
            if key == "private_key":
                env_value = env_value.replace("\\n", "\n")

            return env_value
        return value
    elif isinstance(value, dict):
        return {k: v for k, v in 
                ((k, _expand_value(v, k)) for k, v in value.items()) 
                if v is not None}
    elif isinstance(value, list):
        return [item for item in 
                (_expand_value(item) for item in value) 
                if item is not None]
    else:
        return value


def setup_secrets():
    """Expand environment variables in st.secrets singleton."""
    # Get current secrets as dict
    current_secrets = st.secrets.to_dict()

    # Expand all values
    expanded_secrets = _expand_value(current_secrets)

    # Update the singleton
    secrets_singleton._secrets = expanded_secrets


def get_oauth_access_token(connector_id: str) -> str:
    """Fetch fresh OAuth access token from Modal backend.

    This function is used by Streamlit apps to get access tokens for
    OAuth connectors like Google Drive. The access token is short-lived
    (~1 hour) and should be fetched fresh when needed.

    Args:
        connector_id: The OAuth connector ID (e.g., from st.secrets["gdrive"]["connector_id"])                     

    Returns:
        Access token string for use with provider APIs (Google Drive, etc.)

    Raises:
        ValueError: If required environment variables are not set
        httpx.HTTPStatusError: If the backend request fails

    Example:
        >>> connector_id = st.secrets["gdrive"]["connector_id"]  # UUID
        >>> access_token = get_oauth_access_token(connector_id)
        >>> # Use with Google Drive API
        >>> from google.oauth2.credentials import Credentials
        >>> creds = Credentials(token=access_token)

    Note:
        WORKSHOP_DEPLOYMENT_TOKEN and WORKSHOP_BACKEND_URL are auto-injected
        by the deployment system when OAuth connectors are included in
        the deployment. No manual configuration is needed.
    """
    token = os.environ.get("WORKSHOP_DEPLOYMENT_TOKEN")
    backend_url = os.environ.get("WORKSHOP_BACKEND_URL")

    if not token:
        raise ValueError(
            "WORKSHOP_DEPLOYMENT_TOKEN not set. "
            "Ensure OAuth connectors are included in the deployment."
        )
    if not backend_url:
        raise ValueError(
            "WORKSHOP_BACKEND_URL not set. "
            "Ensure OAuth connectors are included in the deployment."
        )

    response = httpx.get(
        f"{backend_url}/deployments/connectors/{connector_id}/access_token",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()

    data = response.json()
    return data["access_token"]