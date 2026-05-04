import os
import sys
from os import environ

import requests
from dotenv import load_dotenv

env_file_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_file_path)

DREAMHOST_API_BASE = "https://api.dreamhost.com"
DREAMHOST_API_TOKEN = environ.get("DREAMHOST_API_TOKEN", "")
DISCORD_WEBHOOK_ID = environ.get("DISCORD_WEBHOOK_ID", "")
DISCORD_WEBHOOK_TOKEN = environ.get("DISCORD_WEBHOOK_TOKEN", "")
DISCORD_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/"
    f"{DISCORD_WEBHOOK_ID}/{DISCORD_WEBHOOK_TOKEN}"
)


class Dreamhost:
    """Wrapper class for DreamHost API interactions."""

    def __init__(self, api_token: str):
        self.api_token = api_token

    def _build_url(self, command: str, params: dict) -> str:
        """Constructs the full API URL with given command and parameters."""
        base_params = {
            "key": DREAMHOST_API_TOKEN,
            "format": "json",
            "cmd": command,
        }
        if params is None:
            params = {}

        all_params = {**base_params, **params}
        query_string = "&".join(
            f"{key}={value}" for key, value in all_params.items()
        )
        return f"{DREAMHOST_API_BASE}/?{query_string}"

    def get_dns_records(self) -> dict:
        """Fetches all DNS records from DreamHost."""
        url = self._build_url("dns-list_records", {})
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()

    def remove_dns_record(
        self, record: str, record_type: str, record_value: str
    ):
        """Removes a specific DNS record."""
        url = self._build_url(
            "dns-remove_record",
            {
                "record": record,
                "type": record_type,
                "value": record_value,
            },
        )
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()

    def add_dns_record(self, record: str, record_type: str, record_value: str):
        """Adds a specific DNS record."""
        url = self._build_url(
            "dns-add_record",
            {
                "record": record,
                "type": record_type,
                "value": record_value,
            },
        )
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()


def send_discord_notification(message: str):
    """Sends a notification message to a Discord channel via webhook."""
    data = {"content": message}
    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=data,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if response.status_code != 204:
        print(
            f"Failed to send Discord notification: {response.status_code} "
            f"- {response.text}"
        )
    else:
        print("Discord notification sent successfully.")


def get_public_ip(ipv6=False) -> str:
    """Fetches the current public IP address."""
    url = "https://api.ipify.org?format=json"
    if ipv6:
        url = "https://api64.ipify.org?format=json"

    response = requests.get(url, timeout=10)
    response.raise_for_status()
    result = response.json()
    return result.get("ip", "")


# Records to update in the format: "record:type"
# e.g., "subdomain.example.com:A,another.example.com:AAAA"
TARGET_RECORDS = [
    r.strip().split(":")
    for r in environ.get("TARGET_RECORDS", "").split(",")
    if r
]


def main() -> int:
    """Main function."""
    print("Starting DNS updater...")
    print(f"Target records: {TARGET_RECORDS!r}")

    dreamhost = Dreamhost(DREAMHOST_API_TOKEN)
    dns_data = dreamhost.get_dns_records()

    if dns_data.get("result", "error") != "success":
        send_discord_notification(
            "\n".join(
                [
                    ":red_circle: There was an issue fetching DNS records.",
                    "" "Response:",
                    "```json",
                    # Limit to 500 characters to avoid overly long messages
                    str(dns_data)[:500],
                    "```",
                ]
            )
        )
        print("Could not fetch DNS records")
        return 1

    dns_records = dns_data.get("data", [])

    public_ipv4 = get_public_ip(ipv6=False)
    public_ipv6 = get_public_ip(ipv6=True)

    if not public_ipv4 or not public_ipv6:
        print("Could not fetch public IP addresses")
        send_discord_notification(
            ":red_circle: There was an issue fetching public IP addresses."
        )
        return 1

    updates_made = []
    for record_name, record_type in TARGET_RECORDS:
        current_ip = public_ipv4 if record_type == "A" else public_ipv6

        matching_records = [
            r
            for r in dns_records
            if r.get("record") == record_name and r.get("type") == record_type
        ]

        # When no matching records are found, we can choose to add the record
        if not matching_records:
            dreamhost.add_dns_record(record_name, record_type, current_ip)
            update_txt = (
                f":green_circle: Added `{record_name}` "
                f"(`{record_type}`): `{current_ip}`"
            )
            updates_made.append(update_txt)
            print(update_txt.replace("`", ""))

        else:
            for record in matching_records:
                existing_value = record.get("value")
                if existing_value != current_ip:
                    dreamhost.remove_dns_record(
                        record_name, record_type, existing_value
                    )
                    dreamhost.add_dns_record(
                        record_name, record_type, current_ip
                    )
                    update_txt = (
                        f":yellow_circle: Updated `{record_name}` (`{record_type}`): "
                        f"`{existing_value}` -> `{current_ip}`"
                    )
                    updates_made.append(update_txt)
                    print(update_txt.replace("`", ""))

    if updates_made:
        send_discord_notification(
            "\n".join(
                [
                    ":white_check_mark: DNS records updated successfully:",
                    "",
                    *updates_made,
                ]
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
