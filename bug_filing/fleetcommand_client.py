import os
import json
import re
import requests
import logging


def fetch_domino_version(catalog_id, catalog_version):
    """
    Makes an API request to Fleetcommand using the catalog ID and version values.
    Returns the domino version associated with the catalog.
    """
    headers = {"X-API-Token": os.environ["FLEETCOMMAND_USER_API_TOKEN"]}
    response = requests.request(
        "GET",
        f"https://fleetcommand.internal.domino.tech/api/catalog/{catalog_id}/versions/{catalog_version}",
        headers=headers,
    )

    if response.status_code == 200:
        response_text = json.loads(response.text)
        catalog_version = response_text["meta"]["domino_version"]
        domino_version = re.match(r"^([^+]*)", catalog_version).group(1)
        logging.info(f"Successfully retrieved domino version {domino_version}.")
    else:
        raise Exception(
            f"Error: Unable to retrieve domino version from Fleetcommand {response.status_code}."
        )

    return domino_version
