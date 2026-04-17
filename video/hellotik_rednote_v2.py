import argparse
import base64
import json
import os
import random
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


BJ_TIMEZONE = timezone(timedelta(hours=8))
DEFAULT_AES_KEY = "93838338562359368888868323563256"
DEFAULT_XOR_KEY = 90
CUSTOM_B64 = "ZYXABCDEFGHIJKLMNOPQRSTUVWzyxabcdefghijklmnopqrstuvw9876543210-_"
STANDARD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
DEFAULT_BASE_URL = "https://www.hellotik.app"
DEFAULT_REFERER = "https://www.hellotik.app/zh/rednote"
DEFAULT_PARSE_VERSION = 1


@dataclass
class HelloTikParseResult:
    raw: dict[str, Any]

    @property
    def url(self) -> str | None:
        return self.hd_url or self.raw.get("url")

    @property
    def raw_url(self) -> str | None:
        return self.raw.get("url")

    @property
    def video_variants(self) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        for video in self.raw.get("videos") or []:
            info_list = video.get("video_fullinfo") or []
            for item in info_list:
                if isinstance(item, dict) and item.get("url"):
                    variants.append(item)
        return variants

    @property
    def best_video(self) -> dict[str, Any] | None:
        variants = self.video_variants
        if not variants:
            return None
        return max(variants, key=lambda item: int(item.get("size") or 0))

    @property
    def normal_video(self) -> dict[str, Any] | None:
        variants = self.video_variants
        if not variants:
            return None
        for item in variants:
            if item.get("type") == "正常":
                return item
        return min(variants, key=lambda item: int(item.get("size") or 0))

    @property
    def hd_url(self) -> str | None:
        best = self.best_video
        if best:
            return best.get("url")
        return self.raw.get("url")

    @property
    def normal_url(self) -> str | None:
        normal = self.normal_video
        if normal:
            return normal.get("url")
        return self.raw.get("url")

    @property
    def title(self) -> str | None:
        return self.raw.get("title")

    @property
    def type(self) -> str | None:
        return self.raw.get("type")


class HelloTikRednoteClientV2:
    def __init__(
        self,
        *,
        aes_key: str = DEFAULT_AES_KEY,
        xor_key: int = DEFAULT_XOR_KEY,
        base_url: str = DEFAULT_BASE_URL,
        referer: str = DEFAULT_REFERER,
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.aes_key = aes_key
        self.xor_key = xor_key
        self.base_url = base_url.rstrip("/")
        self.referer = referer
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": self.base_url,
                "Referer": self.referer,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
            }
        )

    def parse(
        self,
        source_url: str,
        *,
        is_mobile: bool = False,
        iso_code: str | None = None,
        ad_type: str = "adsense",
        uwx_id: str | None = None,
        user_id: str | int | None = None,
        success_count: int | str | None = None,
        total_success_count: int | str | None = None,
        first_success_date: str | None = None,
        geoip_ip: str | None = None,
        report_stat: bool = False,
    ) -> HelloTikParseResult:
        normalized_source = self.normalize_source(source_url)

        gate = self.fetch_gate_ticket(normalized_source)

        plain_payload = self.build_plain_payload(
            request_url=normalized_source,
            is_mobile=is_mobile,
            iso_code=iso_code,
            ad_type=ad_type,
            uwx_id=uwx_id,
            user_id=user_id,
            success_count=success_count,
            total_success_count=total_success_count,
            first_success_date=first_success_date,
            geoip_ip=geoip_ip,
        )

        encrypted = self.encrypt_parse_payload(
            plain_payload,
            parse_ticket=gate["ticket"],
            enc_seed=gate["seed"],
        )

        request_body = {
            gate["request_fields"]["key"]: encrypted["parseTicket"],
            gate["request_fields"]["payload"]: encrypted["payload"],
            gate["request_fields"]["iv"]: encrypted["iv"],
            gate["request_fields"]["version"]: encrypted["v"],
        }

        response = self.session.post(
            f"{self.base_url}/api/parse",
            json=request_body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(data.get("error") or data.get("message") or "Parse failed")

        raw_data = data.get("data")
        if data.get("encrypt"):
            raw_data = self.generate_output(raw_data, data["key"], self.aes_key)

        if not isinstance(raw_data, dict):
            raise RuntimeError("Unexpected parse response payload")

        if report_stat:
            self.report_parse_stat(normalized_source, "success")

        return HelloTikParseResult(raw=raw_data)

    def fetch_gate_ticket(self, request_url: str) -> dict[str, Any]:
        route = "gate-e5eea8"
        response = self.session.post(
            f"{self.base_url}/api/{route}",
            json={
                "requestURL": request_url,
                "isBatch": False,
                "mode": "single",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError(data.get("message") or data.get("error") or "Gate request failed")

        request_fields = {
            "key": "tk_e5eea8",
            "payload": "pl_e5eea8",
            "iv": "iv_e5eea8",
            "version": "vr_e5eea8",
            "fallback": "fb_e5eea8",
        }
        ticket = data.get("tk_e5eea8")
        seed = data.get("sd_e5eea8")
        expires_at = data.get("ex_e5eea8")
        if not ticket or not seed:
            raise RuntimeError("Gate response missing ticket or seed")

        return {
            "ticket": ticket,
            "seed": seed,
            "expires_at": expires_at,
            "request_fields": request_fields,
        }

    def build_plain_payload(
        self,
        *,
        request_url: str,
        is_mobile: bool,
        iso_code: str | None,
        ad_type: str,
        uwx_id: str | None,
        user_id: str | int | None,
        success_count: int | str | None,
        total_success_count: int | str | None,
        first_success_date: str | None,
        geoip_ip: str | None,
    ) -> dict[str, Any]:
        if success_count is None:
            success_count = os.environ.get("HELLOTIK_SUCCESS_COUNT", "0")
        if total_success_count is None:
            total_success_count = os.environ.get("HELLOTIK_TOTAL_SUCCESS_COUNT", "0")
        if first_success_date is None:
            first_success_date = os.environ.get("HELLOTIK_FIRST_SUCCESS_DATE")
        if geoip_ip is None:
            geoip_ip = os.environ.get("HELLOTIK_GEOIP_IP") or self.fetch_geo_ip()
        if uwx_id is None:
            uwx_id = os.environ.get("HELLOTIK_UWX_ID") or self.generate_uwx_id()

        payload: dict[str, Any] = {
            "requestURL": request_url,
            "isMobile": "true" if is_mobile else "false",
            "isoCode": iso_code or self.fetch_geo_iso_code() or "Other",
            "adType": ad_type,
            "uwx_id": uwx_id,
            "successCount": str(success_count),
            "totalSuccessCount": str(total_success_count),
            "firstSuccessDate": first_success_date,
            "geoipIp": geoip_ip or "",
        }
        if user_id is not None:
            payload["userID"] = user_id
        return payload

    def encrypt_parse_payload(
        self,
        plain_payload: dict[str, Any],
        *,
        parse_ticket: str,
        enc_seed: str,
        version: int = DEFAULT_PARSE_VERSION,
    ) -> dict[str, Any]:
        key_material = f"{parse_ticket}:{enc_seed}".encode("utf-8")
        digest = hashes.Hash(hashes.SHA256())
        digest.update(key_material)
        key = digest.finalize()

        iv = os.urandom(12)
        plaintext = json.dumps(plain_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        return {
            "parseTicket": parse_ticket,
            "payload": base64.b64encode(ciphertext).decode("ascii"),
            "iv": base64.b64encode(iv).decode("ascii"),
            "v": version,
        }

    def report_parse_stat(self, source_url: str, status: str = "success") -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/account/user/api/parse-stat",
            json={
                "source_url": source_url,
                "status": status,
                "record_scope": "ip_only",
                "source": "hello",
                "geoipIp": os.environ.get("HELLOTIK_GEOIP_IP") or self.fetch_geo_ip() or "",
                "isoCode": self.fetch_geo_iso_code() or "",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_geo_iso_code(self) -> str | None:
        try:
            response = self.session.get(
                "https://user.hellotik.app/getip/geoip?minimal=1",
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("isoCode")
        except requests.RequestException:
            return None

    def fetch_geo_ip(self) -> str | None:
        try:
            response = self.session.get(
                "https://user.hellotik.app/getip/geoip?minimal=1",
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("ip")
        except requests.RequestException:
            return None

    def generate_uwx_id(self) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/account/user/api/uwx/generate-id",
                timeout=min(self.timeout, 10),
            )
            response.raise_for_status()
            uwx_id = response.json().get("data", {}).get("uwx_id")
            if uwx_id:
                return uwx_id
        except requests.RequestException:
            pass

        suffix = "".join(random.choice(string.ascii_uppercase) for _ in range(2))
        return f"uwx_{str(int(time.time() * 1000))[-6:]}{self.random_salt(4)}{suffix}"

    @staticmethod
    def normalize_source(source_url: str) -> str:
        patterns = [
            r"https?://xhslink\.com/[^\s]+",
            r"https?://www\.xiaohongshu\.com/[^\s]+",
            r"https?://xiaohongshu\.com/[^\s]+",
        ]
        for pattern in patterns:
            match = re.search(pattern, source_url, flags=re.IGNORECASE)
            if match:
                return match.group(0).rstrip("!,.;)")
        return source_url.strip()

    @staticmethod
    def random_salt(length: int) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(random.choice(alphabet) for _ in range(length))

    @staticmethod
    def today_bj() -> str:
        return datetime.now(BJ_TIMEZONE).strftime("%Y-%m-%d")

    def generate_output(self, encrypted_data: str, encrypted_iv: str, key_str: str) -> dict[str, Any]:
        data = base64.b64decode(encrypted_data).decode("latin1")
        iv = base64.b64decode(encrypted_iv).decode("latin1")
        data = self.xor_string(data)
        iv = self.xor_string(iv)
        data = self.block_reverse(data)
        iv = self.block_reverse(iv)
        data = self.base64_custom_decode(data)
        iv = self.base64_custom_decode(iv)
        return self.aes_decrypt(data, iv, key_str)

    def xor_string(self, value: str, key: int | None = None) -> str:
        real_key = self.xor_key if key is None else key
        return "".join(chr(ord(ch) ^ real_key) for ch in value)

    @staticmethod
    def block_reverse(value: str, block_size: int = 8) -> str:
        pieces: list[str] = []
        for index in range(0, len(value), block_size):
            pieces.append(value[index:index + block_size][::-1])
        return "".join(pieces)

    @staticmethod
    def base64_custom_decode(value: str) -> str:
        translate = {char: STANDARD_B64[idx] for idx, char in enumerate(CUSTOM_B64)}
        return "".join(translate.get(char, char) for char in value)

    @staticmethod
    def aes_decrypt(data_base64: str, iv_base64: str, key_str: str) -> dict[str, Any]:
        key = key_str.encode("utf-8")
        iv = base64.b64decode(iv_base64)
        encrypted_data = base64.b64decode(data_base64)

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(encrypted_data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
        return json.loads(plaintext.decode("utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Rednote content with HelloTik (new protocol)")
    parser.add_argument("source_url", help="A Rednote URL or copied share text")
    parser.add_argument("--mobile", action="store_true", help="Send isMobile=true")
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    parser.add_argument("--report-stat", action="store_true", help="Also report parse-stat")
    parser.add_argument("--iso-code", default=None, help="Override isoCode")
    parser.add_argument("--ad-type", default="adsense", help="Override adType")
    parser.add_argument("--uwx-id", default=None, help="Override uwx_id")
    parser.add_argument("--user-id", default=None, help="Optional userID")
    parser.add_argument("--success-count", default=None, help="Override successCount")
    parser.add_argument("--total-success-count", default=None, help="Override totalSuccessCount")
    parser.add_argument("--first-success-date", default=None, help="Override firstSuccessDate, format YYYY-MM-DD")
    parser.add_argument("--geoip-ip", default=None, help="Override geoipIp")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = HelloTikRednoteClientV2()
    result = client.parse(
        args.source_url,
        is_mobile=args.mobile,
        iso_code=args.iso_code,
        ad_type=args.ad_type,
        uwx_id=args.uwx_id,
        user_id=args.user_id,
        success_count=args.success_count,
        total_success_count=args.total_success_count,
        first_success_date=args.first_success_date,
        geoip_ip=args.geoip_ip,
        report_stat=args.report_stat,
    )

    if args.json:
        output = dict(result.raw)
        output["_selected_url"] = result.url
        output["_hd_url"] = result.hd_url
        output["_normal_url"] = result.normal_url
        output["_video_variants"] = result.video_variants
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(result.url or "")


if __name__ == "__main__":
    main()
