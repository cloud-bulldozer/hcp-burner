#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module to set GCP related variables and validate credentials
"""

import json
import os
import sys


class GCP:
    """GCP Class"""

    def __init__(self, logging, credentials_file):
        self.logging = logging
        self.credentials_file = credentials_file
        self.gcp_credentials = {}

        if credentials_file and os.path.exists(credentials_file):
            self.credentials_file = os.path.abspath(credentials_file)
            self.logging.info(
                f"GCP credentials file found: {self.credentials_file}. Loading account information"
            )
            try:
                with open(self.credentials_file, "r") as f:
                    self.gcp_credentials = json.load(f)
            except json.JSONDecodeError as err:
                self.logging.error(
                    f"Invalid JSON in GCP credentials file {self.credentials_file}: {err}"
                )
                sys.exit("Exiting...")

            required_keys = ["project_id", "client_email", "private_key"]
            missing_keys = [key for key in required_keys if key not in self.gcp_credentials]
            if missing_keys:
                self.logging.error(
                    f"Missing required credentials in file {self.credentials_file}: "
                    f"{', '.join(missing_keys)}"
                )
                sys.exit("Exiting...")

            self.logging.info(f"GCP configuration verified for file {self.credentials_file}")
            self.logging.debug(
                f"GCP Credentials: project_id={self.gcp_credentials.get('project_id', 'N/A')}, "
                f"client_email={self.gcp_credentials.get('client_email', 'N/A')}"
            )
        else:
            self.logging.info(
                "GCP credentials file is not provided or not found; "
                "GOOGLE_APPLICATION_CREDENTIALS / ADC environment variables are being used"
            )

    def set_gcp_envvars(self, project_id, gcp_region):
        """Set ADC / gcloud-related environment vars from the credentials file."""
        if self.credentials_file and os.path.exists(self.credentials_file):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.credentials_file
            os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
            os.environ["CLOUDSDK_CORE_PROJECT"] = project_id
            os.environ["CLOUDSDK_COMPUTE_REGION"] = gcp_region
            os.environ.pop("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", None)
            if (
                self.gcp_credentials.get("project_id")
                and self.gcp_credentials["project_id"] != project_id
            ):
                self.logging.warning(
                    f"Credentials file project_id ({self.gcp_credentials['project_id']}) differs "
                    f"from --gcp-project-id ({project_id}); using --gcp-project-id for API calls"
                )
            self.logging.info(
                f"ADC identity: {self.gcp_credentials.get('client_email')} "
                f"(GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT={project_id})"
            )

    def set_gcp_environment(self, project_id, gcp_region):
        """Return GCP info dict for the platform environment object."""
        gcp = {
            "project_id": project_id,
            "region": gcp_region,
            "credentials_file": self.credentials_file,
        }
        if self.credentials_file and os.path.exists(self.credentials_file):
            gcp["client_email"] = self.gcp_credentials.get("client_email", "")
            gcp["credentials_project_id"] = self.gcp_credentials.get("project_id", "")
        else:
            self.logging.info(
                "GCP credentials file is not provided, so GCP environment variables are being used"
            )
            gcp["client_email"] = os.environ.get("GCP_CLIENT_EMAIL", "")
            gcp["credentials_project_id"] = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        return gcp

    def apply_adc_env(self, env, project_id):
        """Force hypershift/gcloud subprocesses to use the configured SA key + project."""
        if self.credentials_file and os.path.exists(self.credentials_file):
            env["GOOGLE_APPLICATION_CREDENTIALS"] = self.credentials_file
        env["GOOGLE_CLOUD_PROJECT"] = project_id
        env["CLOUDSDK_CORE_PROJECT"] = project_id
        env.pop("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", None)
        return env

    def process_env(self, project_id, extra=None):
        """Env copy for GCP API subprocesses (hypershift create/destroy iam|infra)."""
        env = self.apply_adc_env(os.environ.copy(), project_id)
        if extra:
            env.update(extra)
        return env

    def get_credentials(self):
        """Get GCP credentials dictionary"""
        return self.gcp_credentials
