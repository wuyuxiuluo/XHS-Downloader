import argparse
import base64
import json
import random
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import md5
from typing import Any

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


BJ_TIMEZONE = timezone(timedelta(hours=8))
DEFAULT_SECRET = "TI52hwg30V08ycUO9"
DEFAULT_AES_KEY = "93838338562359368888868323563256"
DEFAULT_XOR_KEY = 90
CUSTOM_B64 = "ZYXABCDEFGHIJKLMNOPQRSTUVWzyxabcdefghijklmnopqrstuvw9876543210-_"
STANDARD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


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


class HelloTikRednoteClient:
    def __init__(
        self,
        *,
        secret: str = DEFAULT_SECRET,
        aes_key: str = DEFAULT_AES_KEY,
        xor_key: int = DEFAULT_XOR_KEY,
        base_url: str = "https://www.hellotik.app",
        referer: str = "https://www.hellotik.app/zh/rednote",
        timeout: int = 60,
        session: requests.Session | None = None,
    ) -> None:
        self.secret = secret
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
                    "Chrome/146.0.0.0 Safari/537.36"
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
        success_count: int | str = 0,
        total_success_count: int | str = 0,
        first_success_date: str | None = None,
    ) -> HelloTikParseResult:
        normalized_source = self.normalize_source(source_url)
        params = {
            "requestURL": normalized_source,
            "isMobile": "true" if is_mobile else "false",
            "isoCode": iso_code or self.fetch_geo_iso_code() or "Other",
            "adType": ad_type,
            "uwx_id": uwx_id or self.generate_uwx_id(),
            "successCount": str(success_count),
            "totalSuccessCount": str(total_success_count),
            "firstSuccessDate": first_success_date or self.today_bj(),
        }
        payload, sign = self.generate_auth_payload(params)
        response = self.session.post(
            f"{self.base_url}/api/parse",
            headers={"X-Auth-Token": sign},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != 0:
            raise RuntimeError(data.get("error") or "Parse failed")
        if data.get("encrypt"):
            data["data"] = self.generate_output(
                data["data"],
                data["key"],
                self.aes_key,
            )
        return HelloTikParseResult(raw=data["data"])

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

    def report_parse_stat(self, source_url: str, status: str = "success") -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/account/user/api/parse-stat",
            json={
                "source_url": source_url,
                "status": status,
                "record_scope": "ip_only",
                "source": "hello",
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

    def generate_auth_payload(self, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        timestamp = int(time.time())
        client_salt = self.random_salt(8)
        sign = self.generate_signature_with_md5(params, client_salt, timestamp, self.secret)
        payload = dict(params)
        payload["time"] = timestamp
        payload["key"] = client_salt
        return payload, sign

    def generate_signature_with_md5(
        self,
        params: dict[str, Any],
        client_salt: str,
        timestamp: int,
        secret: str,
    ) -> str:
        sorted_keys = sorted(params)
        base_string = "&".join(
            f"{key}={self.js_stringify(params[key])}" for key in sorted_keys
        )
        to_hash = f"{base_string}&salt={client_salt}&ts={timestamp}&secret={secret}"
        return self.replace_bd(md5(to_hash.encode("utf-8")).hexdigest())

    @staticmethod
    def replace_bd(value: str) -> str:
        return value.replace("b", "#").replace("d", "F").replace("#", "C")

    @staticmethod
    def js_stringify(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        return str(value)

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
    parser = argparse.ArgumentParser(description="Parse Rednote content with HelloTik")
    parser.add_argument("source_url", help="A Rednote URL or copied share text")
    parser.add_argument("--mobile", action="store_true", help="Send isMobile=true")
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    parser.add_argument("--report-stat", action="store_true", help="Also report parse-stat")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    client = HelloTikRednoteClient()
    result = client.parse(args.source_url, is_mobile=args.mobile)
    if args.report_stat:
        client.report_parse_stat(args.source_url, "success")

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
