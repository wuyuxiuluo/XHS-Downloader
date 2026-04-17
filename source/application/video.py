import base64
import json
import os
import random
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from source.expansion import Namespace

from .request import Html

if TYPE_CHECKING:
    from httpx import AsyncClient

    from ..module import Manager

__all__ = ["Video"]

BJ_TIMEZONE = timezone(timedelta(hours=8))


@dataclass
class HelloTikParseResult:
    raw: dict[str, Any]

    @staticmethod
    def _variant_size(item: dict[str, Any]) -> int:
        try:
            return int(item.get("size") or 0)
        except (TypeError, ValueError):
            return 0

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
        return max(variants, key=self._variant_size)

    @property
    def hd_url(self) -> str | None:
        if best := self.best_video:
            return best.get("url")
        return self.raw.get("url")


class Video:
    VIDEO_LINK = (
        "video",
        "consumer",
        "originVideoKey",
    )
    HELLOTIK_BASE_URL = "https://www.hellotik.app"
    HELLOTIK_REFERER = f"{HELLOTIK_BASE_URL}/zh/rednote"
    HELLOTIK_AES_KEY = "93838338562359368888868323563256"
    HELLOTIK_XOR_KEY = 90
    HELLOTIK_PARSE_VERSION = 1
    CUSTOM_B64 = "ZYXABCDEFGHIJKLMNOPQRSTUVWzyxabcdefghijklmnopqrstuvw9876543210-_"
    STANDARD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    SOURCE_PATTERNS = (
        r"https?://xhslink\.com/[^\s]+",
        r"https?://www\.xiaohongshu\.com/[^\s]+",
        r"https?://xiaohongshu\.com/[^\s]+",
    )
    TRAILING_PUNCTUATION = "!,.;)\u3002\uff01\uff1b\uff0c\u3001\uff1f?"

    def __init__(
        self,
        manager: "Manager | None" = None,
    ) -> None:
        self.client: "AsyncClient | None" = getattr(manager, "request_client", None)
        self.user_agent = (
            getattr(manager, "blank_headers", {}).get("user-agent")
            if manager
            else None
        ) or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        )

    async def deal_video_link(
        self,
        data: Namespace,
        preference="resolution",
        source_url: str | None = None,
    ) -> list:
        if link := await self.get_hellotik_video_link(data, source_url):
            return [Html.format_url(link)]
        return self.generate_video_link(data) or self.get_video_link(data, preference)

    async def get_hellotik_video_link(
        self,
        data: Namespace,
        source_url: str | None = None,
    ) -> str | None:
        if not self.client:
            return None
        if not (normalized := self.build_source_url(data, source_url)):
            return None
        try:
            gate = await self.fetch_gate_ticket(normalized)
            payload = await self.generate_auth_payload(normalized, gate)
            response = await self.client.post(
                f"{self.HELLOTIK_BASE_URL}/api/parse",
                headers=self.hellotik_headers,
                json=payload,
            )
            response.raise_for_status()
            return self.parse_hellotik_payload(response.json())
        except Exception:
            return None

    @property
    def hellotik_headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": self.HELLOTIK_BASE_URL,
            "Referer": self.HELLOTIK_REFERER,
            "User-Agent": self.user_agent,
        }

    @classmethod
    def build_source_url(
        cls,
        data: Namespace,
        source_url: str | None = None,
    ) -> str:
        if source_url:
            return cls.normalize_source(source_url)
        if note_id := data.safe_extract("noteId", ""):
            return f"https://www.xiaohongshu.com/discovery/item/{note_id}"
        return ""

    @classmethod
    def normalize_source(cls, source_url: str) -> str:
        source_url = source_url.strip().rstrip(cls.TRAILING_PUNCTUATION)
        for pattern in cls.SOURCE_PATTERNS:
            if match := re.search(pattern, source_url, flags=re.IGNORECASE):
                source_url = match.group(0).rstrip(cls.TRAILING_PUNCTUATION)
                break
        return re.sub(
            r"(https?://(?:www\.)?xiaohongshu\.com)/explore/",
            r"\1/discovery/item/",
            source_url,
            flags=re.IGNORECASE,
        )

    @classmethod
    def build_parse_params(
        cls,
        normalized_source: str,
    ) -> dict[str, str]:
        return {
            "requestURL": normalized_source,
            "isMobile": "false",
            "isoCode": "Other",
            "adType": "adsense",
            "uwx_id": cls.generate_uwx_id(),
            "successCount": "0",
            "totalSuccessCount": "0",
            "firstSuccessDate": cls.today_bj(),
            "geoipIp": "",
        }

    @classmethod
    def parse_hellotik_payload(cls, payload: dict[str, Any]) -> str | None:
        if payload.get("status") != 0:
            return None
        data = payload.get("data")
        if payload.get("encrypt"):
            encrypted = payload.get("data")
            iv = payload.get("key")
            if not encrypted or not iv:
                return None
            data = cls.generate_output(
                encrypted,
                iv,
                cls.HELLOTIK_AES_KEY,
            )
        if not isinstance(data, dict):
            return None
        return HelloTikParseResult(data).hd_url

    async def fetch_gate_ticket(self, normalized_source: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.HELLOTIK_BASE_URL}/api/gate-e5eea8",
            headers=self.hellotik_headers,
            json={
                "requestURL": normalized_source,
                "isBatch": False,
                "mode": "single",
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise RuntimeError(data.get("message") or data.get("error") or "Gate request failed")
        ticket = data.get("tk_e5eea8")
        seed = data.get("sd_e5eea8")
        if not ticket or not seed:
            raise RuntimeError("Gate response missing ticket or seed")
        return {
            "ticket": ticket,
            "seed": seed,
            "request_fields": {
                "key": "tk_e5eea8",
                "payload": "pl_e5eea8",
                "iv": "iv_e5eea8",
                "version": "vr_e5eea8",
            },
        }

    async def generate_auth_payload(
        self,
        normalized_source: str,
        gate: dict[str, Any],
    ) -> dict[str, Any]:
        params = self.build_parse_params(normalized_source)
        try:
            params["isoCode"] = await self.fetch_geo_value("isoCode") or params["isoCode"]
            params["geoipIp"] = await self.fetch_geo_value("ip") or params["geoipIp"]
            params["uwx_id"] = await self.fetch_uwx_id() or params["uwx_id"]
        except Exception:
            pass
        encrypted = self.encrypt_parse_payload(
            params,
            parse_ticket=gate["ticket"],
            enc_seed=gate["seed"],
        )
        fields = gate["request_fields"]
        return {
            fields["key"]: encrypted["parseTicket"],
            fields["payload"]: encrypted["payload"],
            fields["iv"]: encrypted["iv"],
            fields["version"]: encrypted["v"],
        }

    def encrypt_parse_payload(
        self,
        plain_payload: dict[str, Any],
        *,
        parse_ticket: str,
        enc_seed: str,
        version: int = HELLOTIK_PARSE_VERSION,
    ) -> dict[str, Any]:
        key_material = f"{parse_ticket}:{enc_seed}".encode("utf-8")
        digest = hashes.Hash(hashes.SHA256())
        digest.update(key_material)
        key = digest.finalize()
        iv = os.urandom(12)
        plaintext = json.dumps(
            plain_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        return {
            "parseTicket": parse_ticket,
            "payload": base64.b64encode(ciphertext).decode("ascii"),
            "iv": base64.b64encode(iv).decode("ascii"),
            "v": version,
        }

    async def fetch_geo_value(self, key: str) -> str | None:
        response = await self.client.get(
            "https://user.hellotik.app/getip/geoip?minimal=1",
            headers=self.hellotik_headers,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get(key)

    async def fetch_uwx_id(self) -> str | None:
        response = await self.client.post(
            f"{self.HELLOTIK_BASE_URL}/account/user/api/uwx/generate-id",
            headers=self.hellotik_headers,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("uwx_id")

    @staticmethod
    def random_salt(length: int) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(random.choice(alphabet) for _ in range(length))

    @staticmethod
    def today_bj() -> str:
        return datetime.now(BJ_TIMEZONE).strftime("%Y-%m-%d")

    @classmethod
    def generate_output(
        cls,
        encrypted_data: str,
        encrypted_iv: str,
        key_str: str,
    ) -> dict[str, Any]:
        data = base64.b64decode(encrypted_data).decode("latin1")
        iv = base64.b64decode(encrypted_iv).decode("latin1")
        data = cls.xor_string(data)
        iv = cls.xor_string(iv)
        data = cls.block_reverse(data)
        iv = cls.block_reverse(iv)
        data = cls.base64_custom_decode(data)
        iv = cls.base64_custom_decode(iv)
        return cls.aes_decrypt(data, iv, key_str)

    @classmethod
    def xor_string(cls, value: str, key: int | None = None) -> str:
        real_key = cls.HELLOTIK_XOR_KEY if key is None else key
        return "".join(chr(ord(ch) ^ real_key) for ch in value)

    @staticmethod
    def block_reverse(value: str, block_size: int = 8) -> str:
        pieces: list[str] = []
        for index in range(0, len(value), block_size):
            pieces.append(value[index : index + block_size][::-1])
        return "".join(pieces)

    @classmethod
    def base64_custom_decode(cls, value: str) -> str:
        translate = {
            char: cls.STANDARD_B64[idx] for idx, char in enumerate(cls.CUSTOM_B64)
        }
        return "".join(translate.get(char, char) for char in value)

    @staticmethod
    def aes_decrypt(
        data_base64: str,
        iv_base64: str,
        key_str: str,
    ) -> dict[str, Any]:
        key = key_str.encode("utf-8")
        iv = base64.b64decode(iv_base64)
        encrypted_data = base64.b64decode(data_base64)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(encrypted_data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
        return json.loads(plaintext.decode("utf-8"))

    @classmethod
    def generate_video_link(cls, data: Namespace) -> list:
        return (
            [Html.format_url(f"https://sns-video-bd.xhscdn.com/{t}")]
            if (t := data.safe_extract(".".join(cls.VIDEO_LINK)))
            else []
        )

    @classmethod
    def get_video_link(
        cls,
        data: Namespace,
        preference="resolution",
    ) -> list:
        if not (items := cls.get_video_items(data)):
            return []
        match preference:
            case "resolution":
                items.sort(key=lambda x: x.height)
            case "bitrate":
                items.sort(key=lambda x: x.videoBitrate)
            case "size":
                items.sort(key=lambda x: x.size)
            case _:
                raise ValueError(f"Invalid video preference value: {preference}")
        return [b[0]] if (b := items[-1].backupUrls) else [items[-1].masterUrl]

    @staticmethod
    def get_video_items(data: Namespace) -> list:
        h264 = data.safe_extract("video.media.stream.h264", [])
        h265 = data.safe_extract("video.media.stream.h265", [])
        return [*h264, *h265]

    @staticmethod
    def generate_uwx_id() -> str:
        suffix = "".join(random.choice(string.ascii_uppercase) for _ in range(2))
        salt = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        return f"uwx_{str(int(time.time() * 1000))[-6:]}{salt}{suffix}"
