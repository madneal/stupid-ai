#!/usr/bin/env python3
"""
Download Xiaomi Home / Mi Home camera cloud recordings from the local Mac app
session.

This follows the code path found in the installed app:
playlist -> /common/app/m3u8 -> HLS segments.
It reads credentials from the local Xiaomi Home App Group preferences and never
prints token values.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookies
import json
import os
import plistlib
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from Crypto.Cipher import AES, ARC4
    from Crypto.Util.Padding import unpad
except Exception as exc:  # pragma: no cover - runtime dependency check
    AES = None
    ARC4 = None
    unpad = None
    CRYPTO_IMPORT_ERROR = exc
else:
    CRYPTO_IMPORT_ERROR = None


APP_GROUP_DIR = Path.home() / "Library/Group Containers/group.com.xiaomi.mihome"
PREFS_PATH = APP_GROUP_DIR / "Library/Preferences/group.com.xiaomi.mihome.plist"
WIDGET_DB_PATH = APP_GROUP_DIR / "widget_db_backUp.sqlite3"
DEFAULT_USER_AGENT = (
    "MiHome/11.3.201 (iPhone; iOS 18.0; Scale/3.00) "
    "XiaomiHomeDownloader/0.1"
)


@dataclass(frozen=True)
class LocalSession:
    user_id: str
    service_token: str
    ssecurity: str
    pass_token: str
    device_id: str
    server_code: str
    language: str
    country_code: str


@dataclass(frozen=True)
class Nonce:
    raw: bytes
    base64_value: str


@dataclass(frozen=True)
class SdCachedVideo:
    did: str
    file_id: str
    path: Path
    size: int
    mtime: float


class MijiaError(RuntimeError):
    pass


class HttpStatusError(MijiaError):
    def __init__(self, code: int, url: str, body: str):
        super().__init__(f"HTTP {code} from {url}: {body[:500]}")
        self.code = code
        self.url = url
        self.body = body


def mihome_document_dirs() -> list[Path]:
    docs: list[Path] = []
    containers = Path.home() / "Library/Containers"
    if containers.exists():
        for path in sorted(containers.glob("*/Data/Documents")):
            if (path / "STDStorage").exists() or any(path.glob("*_mihome.sqlite")):
                docs.append(path)
    return docs


def find_mihome_device_db() -> Path:
    candidates: list[Path] = []
    for docs in mihome_document_dirs():
        candidates.extend(sorted(docs.glob("*_mihome.sqlite")))
    if not candidates:
        raise MijiaError("Local Xiaomi Home device database not found")
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def require_crypto() -> None:
    if CRYPTO_IMPORT_ERROR is not None:
        raise MijiaError(
            "PyCryptodome is required. Current Python cannot import Crypto: "
            f"{CRYPTO_IMPORT_ERROR}"
        )


def load_local_session(path: Path = PREFS_PATH) -> LocalSession:
    if not path.exists():
        raise MijiaError(f"Xiaomi Home preferences not found: {path}")
    with path.open("rb") as fp:
        prefs = plistlib.load(fp)

    missing = [
        key
        for key in ("userId", "serviceToken", "ssecurity")
        if not prefs.get(key)
    ]
    if missing:
        raise MijiaError(
            "Local Xiaomi Home session is incomplete; missing "
            + ", ".join(missing)
        )

    return LocalSession(
        user_id=str(prefs["userId"]),
        service_token=str(prefs["serviceToken"]),
        ssecurity=str(prefs["ssecurity"]),
        pass_token=str(prefs.get("passToken") or ""),
        device_id=str(prefs.get("deviceId") or ""),
        server_code=str(prefs.get("serverCode") or prefs.get("countryCode") or "cn"),
        language=str(prefs.get("language") or prefs.get("mihome.app.language") or "zh_CN"),
        country_code=str(prefs.get("countryCode") or "cn"),
    )


def camera_host(server_code: str) -> str:
    # The app stores both CN and overseas camera domains. CN uses api.io.mi.com;
    # overseas builds use api.mijia.tech.
    if server_code.lower() == "cn":
        return "business.smartcamera.api.io.mi.com"
    return "business.smartcamera.api.mijia.tech"


def generate_nonce(time_diff: float = 0.0) -> Nonce:
    # MJFNonce: 8 random bytes + big-endian int(time / 60), then base64.
    minute = int((time.time() + time_diff) / 60)
    raw = os.urandom(8) + struct.pack(">I", minute)
    return Nonce(raw=raw, base64_value=base64.b64encode(raw).decode("ascii"))


def session_security(raw_ssecurity_b64: str, nonce: Nonce) -> str:
    raw_security = base64.b64decode(raw_ssecurity_b64)
    digest = hashlib.sha256(raw_security + nonce.raw).digest()
    return base64.b64encode(digest).decode("ascii")


def rc4_drop_cipher(session_security_b64: str):
    require_crypto()
    key = base64.b64decode(session_security_b64)
    if len(key) != 32:
        raise MijiaError(f"RC4 key length should be 32 bytes, got {len(key)}")
    cipher = ARC4.new(key)
    cipher.encrypt(b"\x00" * 1024)
    return cipher


def rc4_encrypt_to_b64(cipher, value: Any) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    encrypted = cipher.encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def rc4_decrypt_from_b64(session_security_b64: str, value: str) -> bytes:
    cipher = rc4_drop_cipher(session_security_b64)
    return cipher.decrypt(base64.b64decode(value))


def signature(method: str, request_url: str, params: dict[str, Any], security: str) -> str:
    parsed = urllib.parse.urlparse(request_url)
    pieces: list[str] = []
    if method:
        pieces.append(method.upper())
    if parsed.path:
        pieces.append(parsed.path)
    for key in sorted(params):
        pieces.append(f"{key}={params[key]}")
    pieces.append(security)
    text = "&".join(pieces)
    return base64.b64encode(hashlib.sha1(text.encode("utf-8")).digest()).decode("ascii")


def encrypt_params(
    method: str,
    request_url: str,
    params: dict[str, Any],
    nonce: Nonce,
    session_security_b64: str,
) -> dict[str, str]:
    # Mirrors +[MJFCodingService encryptParam:subUrl:method:nonce:sessionSecurity:].
    signed: dict[str, Any] = dict(params)
    signed["rc4_hash__"] = signature(method, request_url, signed, session_security_b64)

    cipher = rc4_drop_cipher(session_security_b64)
    encrypted: dict[str, str] = {}
    for key in sorted(signed):
        encrypted[key] = rc4_encrypt_to_b64(cipher, signed[key])

    encrypted["signature"] = signature(method, request_url, encrypted, session_security_b64)
    encrypted["_nonce"] = nonce.base64_value
    return encrypted


def cookie_header(session: LocalSession) -> str:
    return (
        f"userId={session.user_id}; "
        f"serviceToken={session.service_token}; "
        f"locale={session.language}; "
        f"countryCode={session.country_code}"
    )


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> bytes:
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise HttpStatusError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise MijiaError(f"Network error for {url}: {exc}") from exc


def http_request_with_headers(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[bytes, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise HttpStatusError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise MijiaError(f"Network error for {url}: {exc}") from exc


def strip_xiaomi_json_prefix(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", "replace")
    prefix = "&&&START&&&"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return json.loads(text)


def service_token_from_headers(headers: Any) -> str | None:
    values = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else []
    jar = http.cookies.SimpleCookie()
    for value in values or []:
        jar.load(value)
    morsel = jar.get("serviceToken")
    return morsel.value if morsel else None


def refresh_service_token(session: LocalSession, sid: str = "mijia") -> LocalSession:
    if not session.pass_token:
        raise MijiaError("Local passToken is missing; cannot refresh serviceToken")
    url = "https://account.xiaomi.com/pass/serviceLogin?" + urllib.parse.urlencode(
        {"sid": sid, "_json": "true"}
    )
    cookie = f"userId={session.user_id}; passToken={session.pass_token}"
    if session.device_id:
        cookie += f"; deviceId={session.device_id}"
    raw, _headers = http_request_with_headers(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": cookie,
        },
        timeout=30,
    )
    data = strip_xiaomi_json_prefix(raw)
    location = data.get("location")
    if not location:
        code = data.get("code")
        desc = data.get("desc") or data.get("description") or data.get("message")
        raise MijiaError(f"serviceLogin did not return a token location: code={code} {desc}")

    _raw2, headers2 = http_request_with_headers(
        location,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Cookie": cookie,
        },
        timeout=30,
    )
    service_token = service_token_from_headers(headers2)
    if not service_token:
        raise MijiaError("serviceLogin location did not return serviceToken cookie")

    return LocalSession(
        user_id=session.user_id,
        service_token=service_token,
        ssecurity=str(data.get("ssecurity") or session.ssecurity),
        pass_token=session.pass_token,
        device_id=session.device_id,
        server_code=session.server_code,
        language=session.language,
        country_code=session.country_code,
    )


def decode_api_response(raw: bytes, session_security_b64: str) -> Any:
    text = raw.decode("utf-8", "replace")

    def parse_json_or_text(data: bytes | str) -> Any:
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data

    parsed = parse_json_or_text(text)
    if isinstance(parsed, dict):
        for key in ("result", "data"):
            value = parsed.get(key)
            if isinstance(value, str):
                try:
                    decrypted = rc4_decrypt_from_b64(session_security_b64, value)
                except Exception:
                    continue
                parsed[key] = parse_json_or_text(decrypted)
                return parsed
        return parsed

    try:
        decrypted = rc4_decrypt_from_b64(session_security_b64, text)
    except Exception:
        return parsed
    return parse_json_or_text(decrypted)


def validate_api_response(response: Any) -> Any:
    if isinstance(response, dict) and "code" in response:
        code = response.get("code")
        try:
            ok = int(code) == 0
        except (TypeError, ValueError):
            ok = str(code).lower() in {"ok", "success"}
        if not ok:
            message = response.get("message") or response.get("desc") or response.get("description")
            raise MijiaError(f"Xiaomi API returned code={code}: {message}")
    return response


def camera_api_request(
    session: LocalSession,
    api: str,
    payload: dict[str, Any],
    *,
    method: str = "POST",
    allow_refresh: bool = True,
) -> Any:
    url = f"https://{camera_host(session.server_code)}{api}"
    nonce = generate_nonce()
    sec = session_security(session.ssecurity, nonce)
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    body_params = encrypt_params(method, url, {"data": data_json}, nonce, sec)
    body: bytes | None
    request_url = url
    if method.upper() == "GET":
        request_url = url + "?" + urllib.parse.urlencode(body_params)
        body = None
    else:
        body = urllib.parse.urlencode(body_params).encode("utf-8")
    try:
        raw = http_request(
            request_url,
            method=method,
            data=body,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie_header(session),
                "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
                "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            },
        )
    except HttpStatusError as exc:
        if allow_refresh and exc.code == 401 and "serviceToken invalid" in exc.body:
            refreshed = refresh_service_token(session)
            return camera_api_request(
                refreshed,
                api,
                payload,
                method=method,
                allow_refresh=False,
            )
        raise
    return validate_api_response(decode_api_response(raw, sec))


def parse_time_arg(value: str, unit: str) -> int:
    if re.fullmatch(r"\d{10,13}", value):
        number = int(value)
        if unit == "ms" and number < 10_000_000_000:
            return number * 1000
        if unit == "s" and number > 10_000_000_000:
            return number // 1000
        return number

    dt = datetime.fromisoformat(value.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    seconds = int(dt.timestamp())
    return seconds * 1000 if unit == "ms" else seconds


def list_local_devices(db_path: Path = WIDGET_DB_PATH) -> list[dict[str, Any]]:
    if not db_path.exists():
        if db_path == WIDGET_DB_PATH:
            return list_runtime_devices()
        raise MijiaError(f"Local device database not found: {db_path}")
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select did, name, model, categoryId, parentCategoryId, isOnline "
            "from device order by name"
        ).fetchall()
    except sqlite3.Error:
        if db_path == WIDGET_DB_PATH:
            return list_runtime_devices()
        raise
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass
    return [dict(row) for row in rows]


def list_runtime_devices(db_path: Path | None = None) -> list[dict[str, Any]]:
    if db_path is None:
        db_path = find_mihome_device_db()
    if not db_path.exists():
        raise MijiaError(f"Local device database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "select ZDID as did, ZNAME as name, ZMODEL as model, "
            "ZISONLINE as isOnline, 0 as categoryId, 0 as parentCategoryId "
            "from ZDEVICE order by ZNAME"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def find_device(did: str, db_path: Path = WIDGET_DB_PATH) -> dict[str, Any] | None:
    for device in list_local_devices(db_path):
        if str(device.get("did")) == str(did):
            return device
    return None


def find_device_runtime_info(
    did: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    if db_path is None:
        try:
            db_path = find_mihome_device_db()
        except MijiaError:
            return None
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "select ZDID, ZMODEL, ZNAME, ZLOCALIP, ZMAC, ZEXTP2PID, ZRSSI, ZISONLINE "
            "from ZDEVICE where ZDID = ?",
            (str(did),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def iter_dicts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_dicts(value)


def extract_file_items(response: Any) -> list[dict[str, Any]]:
    items = []
    seen: set[str] = set()
    for item in iter_dicts(response):
        file_id = item.get("fileId") or item.get("file_id") or item.get("fileid")
        if file_id is None:
            continue
        file_id = str(file_id)
        if file_id in seen:
            continue
        seen.add(file_id)
        items.append(item)
    return items


def extract_first_m3u8_url(response: Any) -> str:
    for item in iter_dicts(response):
        for value in item.values():
            if isinstance(value, str) and ".m3u8" in value:
                return value
    if isinstance(response, str) and "#EXTM3U" in response:
        return response
    raise MijiaError("m3u8 URL was not found in API response")


def playlist_request(
    session: LocalSession,
    *,
    did: str,
    begin_time: int,
    end_time: int,
    limit: int,
    source: str,
) -> Any:
    api = (
        "/miot/camera/app/v1/alarm/playlist/limit"
        if source == "alarm"
        else "/miot/camera/app/v1/cloud/playlist/limit"
    )
    return camera_api_request(
        session,
        api,
        {
            "did": did,
            "region": session.server_code,
            "language": session.language,
            "beginTime": begin_time,
            "endTime": end_time,
            "limit": limit,
        },
        method="GET",
    )


def m3u8_request(
    session: LocalSession,
    *,
    did: str,
    file_id: str,
    model: str,
    is_alarm: bool,
) -> str:
    response = camera_api_request(
        session,
        "/common/app/m3u8",
        {
            "did": did,
            "fileId": file_id,
            "model": model,
            "isAlarm": bool(is_alarm),
        },
        method="GET",
    )
    return extract_first_m3u8_url(response)


def parse_attrs(line: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line):
        key, value = match.groups()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[key] = value
    return attrs


def url_join(base_url: str, maybe_relative: str) -> str:
    return urllib.parse.urljoin(base_url, maybe_relative)


def load_playlist_text(url_or_text: str, headers: dict[str, str]) -> tuple[str, str]:
    if "#EXTM3U" in url_or_text:
        return url_or_text, ""
    raw = http_request(url_or_text, headers=headers, timeout=60)
    return raw.decode("utf-8", "replace"), url_or_text


def choose_media_playlist(text: str, base_url: str, headers: dict[str, str]) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[str] = []
    expect_variant = False
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            expect_variant = True
            continue
        if expect_variant and not line.startswith("#"):
            variants.append(url_join(base_url, line))
            expect_variant = False
    if not variants:
        return text, base_url
    chosen = variants[-1]
    media_raw = http_request(chosen, headers=headers, timeout=60)
    return media_raw.decode("utf-8", "replace"), chosen


def media_segments(text: str, base_url: str, headers: dict[str, str]) -> list[tuple[str, bytes | None, bytes | None]]:
    segments: list[tuple[str, bytes | None, bytes | None]] = []
    key: bytes | None = None
    iv: bytes | None = None
    sequence = 0
    current_sequence = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
            current_sequence = sequence
            continue
        if line.startswith("#EXT-X-KEY:"):
            attrs = parse_attrs(line.split(":", 1)[1])
            method = attrs.get("METHOD", "")
            if method == "NONE":
                key = None
                iv = None
                continue
            if method != "AES-128":
                raise MijiaError(f"Unsupported HLS key method: {method}")
            key_url = url_join(base_url, attrs["URI"])
            key = http_request(key_url, headers=headers, timeout=60)
            if len(key) != 16:
                raise MijiaError(f"HLS AES key should be 16 bytes, got {len(key)}")
            if "IV" in attrs:
                iv_text = attrs["IV"]
                if iv_text.startswith("0x"):
                    iv_text = iv_text[2:]
                iv = bytes.fromhex(iv_text.zfill(32))
            else:
                iv = None
            continue
        if line.startswith("#"):
            continue
        seg_iv = iv or current_sequence.to_bytes(16, "big")
        segments.append((url_join(base_url, line), key, seg_iv))
        current_sequence += 1

    return segments


def decrypt_hls_segment(data: bytes, key: bytes | None, iv: bytes | None) -> bytes:
    if not key:
        return data
    require_crypto()
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    decrypted = cipher.decrypt(data)
    try:
        return unpad(decrypted, AES.block_size)
    except ValueError:
        return decrypted


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.strip("._") or "video"


def item_label(item: dict[str, Any]) -> str:
    for keys in (
        ("beginTime", "endTime"),
        ("startTime", "endTime"),
        ("timeStart", "timeEnd"),
    ):
        if keys[0] in item and keys[1] in item:
            return f"{item[keys[0]]}-{item[keys[1]]}"
    return str(item.get("fileId") or "video")


def app_sd_video_dirs(did: str) -> list[Path]:
    roots: list[Path] = []
    for docs in mihome_document_dirs():
        roots.extend(
            [
                docs / "STDStorage" / str(did) / "sdVideo" / str(did),
                docs / "sdVideo" / str(did),
            ]
        )
    return [path for path in roots if path.exists()]


def iter_sd_cached_videos(
    did: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[SdCachedVideo]:
    videos: list[SdCachedVideo] = []
    seen: set[Path] = set()
    for directory in app_sd_video_dirs(did):
        for path in sorted(directory.glob("*.mp4")):
            if path in seen:
                continue
            seen.add(path)
            file_id = path.stem
            if not re.fullmatch(r"\d{10,13}", file_id):
                continue
            timestamp = int(file_id)
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            if start_ms is not None and timestamp < start_ms:
                continue
            if end_ms is not None and timestamp > end_ms:
                continue
            stat = path.stat()
            videos.append(
                SdCachedVideo(
                    did=str(did),
                    file_id=file_id,
                    path=path,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
    return videos


def local_time_text(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def safe_sd_output_name(video: SdCachedVideo) -> str:
    timestamp = int(video.file_id)
    if timestamp < 10_000_000_000:
        timestamp *= 1000
    label = datetime.fromtimestamp(timestamp / 1000).strftime("%Y%m%d_%H%M%S")
    return safe_name(f"{video.did}_{label}_{video.file_id}") + ".mp4"


def copy_sd_video(video: SdCachedVideo, out_dir: Path, overwrite: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / safe_sd_output_name(video)
    if target.exists() and not overwrite:
        raise MijiaError(f"Output already exists: {target}")
    tmp = target.with_suffix(target.suffix + ".part")
    shutil.copy2(video.path, tmp)
    tmp.replace(target)
    return target


def encode_rdt_u32_command(
    command: int,
    *,
    timestamp_ms: int | None = None,
    channel: int | None = None,
) -> str:
    # Matches the React Native bundle:
    # command 1/5: Uint32Array(24); word0=cmd; word1=4; timestamp bytes at
    # offset 8; channel bytes at offset 16. JS writes on little-endian arm64.
    if command in {1, 5}:
        if timestamp_ms is None:
            raise MijiaError("timestamp is required for RDT command 1/5")
        timestamp_s = timestamp_ms // 1000
        frame = bytearray(24)
        struct.pack_into("<I", frame, 0, command)
        struct.pack_into("<I", frame, 4, 4)
        struct.pack_into("<I", frame, 8, timestamp_s)
        struct.pack_into("<I", frame, 16, int(channel or 0))
        return base64.b64encode(frame).decode("ascii")
    if command == 6:
        frame = bytearray(24)
        struct.pack_into("<I", frame, 0, command)
        struct.pack_into("<I", frame, 16, int(channel or 0))
        return base64.b64encode(frame).decode("ascii")
    if command == 16:
        if timestamp_ms is None:
            raise MijiaError("timestamp is required for RDT command 16")
        timestamp_s = timestamp_ms // 1000
        frame = bytearray(12)
        struct.pack_into("<I", frame, 0, command)
        struct.pack_into("<I", frame, 4, 4)
        struct.pack_into("<I", frame, 8, timestamp_s)
        return base64.b64encode(frame).decode("ascii")
    raise MijiaError(f"Unsupported RDT command for encoder: {command}")


def download_hls(
    m3u8_url_or_text: str,
    output_path: Path,
    *,
    headers: dict[str, str],
    overwrite: bool,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise MijiaError(f"Output already exists: {output_path}")

    text, base_url = load_playlist_text(m3u8_url_or_text, headers)
    text, base_url = choose_media_playlist(text, base_url, headers)
    segments = media_segments(text, base_url, headers)
    if not segments:
        raise MijiaError("No HLS segments found in playlist")

    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    with tmp_path.open("wb") as fp:
        for index, (segment_url, key, iv) in enumerate(segments, start=1):
            data = http_request(segment_url, headers=headers, timeout=120)
            fp.write(decrypt_hls_segment(data, key, iv))
            print(f"segment {index}/{len(segments)}", file=sys.stderr)
    tmp_path.replace(output_path)
    return output_path


def remux_with_ffmpeg(input_ts: Path, output_mp4: Path, overwrite: bool) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MijiaError("ffmpeg is not installed; kept TS output instead")
    if output_mp4.exists() and not overwrite:
        raise MijiaError(f"Output already exists: {output_mp4}")
    cmd = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-i",
        str(input_ts),
        "-c",
        "copy",
        str(output_mp4),
    ]
    subprocess.run(cmd, check=True)
    return output_mp4


def print_devices(args: argparse.Namespace) -> int:
    devices = list_local_devices()
    for dev in devices:
        if args.camera_only:
            text = f"{dev.get('name','')} {dev.get('model','')}".lower()
            if "camera" not in text and "摄像" not in text:
                continue
        print(
            "\t".join(
                [
                    str(dev.get("did", "")),
                    str(dev.get("model", "")),
                    str(dev.get("name", "")),
                    "online" if dev.get("isOnline") else "offline",
                ]
            )
        )
    return 0


def print_cloud_list(args: argparse.Namespace) -> int:
    session = refresh_service_token(load_local_session(), args.sid)
    response = playlist_request(
        session,
        did=args.did,
        begin_time=parse_time_arg(args.start, args.time_unit),
        end_time=parse_time_arg(args.end, args.time_unit),
        limit=args.limit,
        source=args.source,
    )
    items = extract_file_items(response)
    for item in items:
        print(f"{item.get('fileId')}\t{item_label(item)}")
    print(f"total={len(items)}", file=sys.stderr)
    return 0


def download_command(args: argparse.Namespace) -> int:
    session = refresh_service_token(load_local_session(), args.sid)
    model = args.model
    if not model:
        dev = find_device(args.did)
        if dev:
            model = str(dev.get("model") or "")
    if not model:
        raise MijiaError("model is required when it is not present in the local device DB")

    if args.file_id:
        items = [{"fileId": args.file_id}]
    else:
        response = playlist_request(
            session,
            did=args.did,
            begin_time=parse_time_arg(args.start, args.time_unit),
            end_time=parse_time_arg(args.end, args.time_unit),
            limit=args.limit,
            source=args.source,
        )
        items = extract_file_items(response)

    if not items:
        raise MijiaError("No cloud recording items found")

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Cookie": cookie_header(session),
    }
    out_dir = Path(args.out).expanduser()
    for item in items:
        file_id = str(item.get("fileId") or item.get("file_id") or item.get("fileid"))
        m3u8 = m3u8_request(
            session,
            did=args.did,
            file_id=file_id,
            model=model,
            is_alarm=args.source == "alarm" or args.alarm,
        )
        name = safe_name(f"{item_label(item)}_{file_id}")
        ts_path = out_dir / f"{name}.ts"
        print(f"downloading fileId={file_id}", file=sys.stderr)
        download_hls(m3u8, ts_path, headers=headers, overwrite=args.overwrite)
        if args.mp4:
            try:
                remux_with_ffmpeg(ts_path, out_dir / f"{name}.mp4", args.overwrite)
            except MijiaError as exc:
                print(str(exc), file=sys.stderr)
        print(ts_path)
    return 0


def print_sd_info(args: argparse.Namespace) -> int:
    dev = find_device(args.did)
    runtime = find_device_runtime_info(args.did) or {}
    cache_dirs = app_sd_video_dirs(args.did)
    print(f"did\t{args.did}")
    if dev:
        print(f"name\t{dev.get('name', '')}")
        print(f"model\t{dev.get('model', '')}")
        print(f"online\t{1 if dev.get('isOnline') else 0}")
    if runtime:
        print(f"local_ip\t{runtime.get('ZLOCALIP') or ''}")
        print(f"mac\t{runtime.get('ZMAC') or ''}")
        print(f"rssi\t{runtime.get('ZRSSI') or ''}")
    for path in cache_dirs:
        print(f"sd_cache_dir\t{path}")
    if not cache_dirs:
        print(f"sd_cache_dir\t")
    print("rdt_download_command\t1")
    print("rdt_thumbnail_command\t5")
    print("rdt_file_list_command\t6")
    print("rdt_ts_download_command\t16")
    return 0


def print_sd_cached_list(args: argparse.Namespace) -> int:
    start_ms = parse_time_arg(args.start, "ms") if args.start else None
    end_ms = parse_time_arg(args.end, "ms") if args.end else None
    videos = iter_sd_cached_videos(args.did, start_ms=start_ms, end_ms=end_ms)
    for video in videos:
        timestamp = int(video.file_id)
        if timestamp < 10_000_000_000:
            timestamp *= 1000
        print(
            "\t".join(
                [
                    video.file_id,
                    local_time_text(timestamp),
                    str(video.size),
                    str(video.path),
                ]
            )
        )
    print(f"total={len(videos)}", file=sys.stderr)
    return 0


def sd_download_command(args: argparse.Namespace) -> int:
    start_ms = parse_time_arg(args.start, "ms") if args.start else None
    end_ms = parse_time_arg(args.end, "ms") if args.end else None
    videos = iter_sd_cached_videos(args.did, start_ms=start_ms, end_ms=end_ms)
    if args.file_id:
        wanted = str(args.file_id)
        videos = [video for video in videos if video.file_id == wanted]
        if not videos:
            all_cached = iter_sd_cached_videos(args.did)
            videos = [video for video in all_cached if video.file_id == wanted]
    if not videos:
        raise MijiaError(
            "No cached SD videos found. Open Xiaomi Home's SD playback once and "
            "download the wanted clip there; this command exports the MP4 that "
            "the app stores after its verified RDT transfer."
        )

    out_dir = Path(args.out).expanduser()
    for video in videos:
        target = copy_sd_video(video, out_dir, args.overwrite)
        print(target)
    return 0


def print_sd_rdt_command(args: argparse.Namespace) -> int:
    timestamp_ms = parse_time_arg(args.timestamp, "ms") if args.timestamp else None
    print(
        encode_rdt_u32_command(
            args.rdt_command,
            timestamp_ms=timestamp_ms,
            channel=args.channel,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xiaomi Home camera video downloader")
    sub = parser.add_subparsers(dest="command", required=True)

    p_devices = sub.add_parser("devices", help="list local Xiaomi Home devices")
    p_devices.add_argument("--camera-only", action="store_true")
    p_devices.set_defaults(func=print_devices)

    p_list = sub.add_parser("list", help="list camera cloud recording fileIds")
    p_list.add_argument("--did", required=True)
    p_list.add_argument("--start", required=True, help="ISO time or unix timestamp")
    p_list.add_argument("--end", required=True, help="ISO time or unix timestamp")
    p_list.add_argument("--limit", type=int, default=100)
    p_list.add_argument("--source", choices=["cloud", "alarm"], default="cloud")
    p_list.add_argument("--time-unit", choices=["ms", "s"], default="ms")
    p_list.add_argument("--sid", default="xiaomiio", choices=["mijia", "xiaomiio"])
    p_list.set_defaults(func=print_cloud_list)

    p_download = sub.add_parser("download", help="download camera cloud recordings")
    p_download.add_argument("--did", required=True)
    p_download.add_argument("--model")
    p_download.add_argument("--file-id")
    p_download.add_argument("--start", help="ISO time or unix timestamp")
    p_download.add_argument("--end", help="ISO time or unix timestamp")
    p_download.add_argument("--limit", type=int, default=100)
    p_download.add_argument("--source", choices=["cloud", "alarm"], default="cloud")
    p_download.add_argument("--sid", default="xiaomiio", choices=["mijia", "xiaomiio"])
    p_download.add_argument("--alarm", action="store_true")
    p_download.add_argument("--time-unit", choices=["ms", "s"], default="ms")
    p_download.add_argument("--out", default="mijia-downloads")
    p_download.add_argument("--overwrite", action="store_true")
    p_download.add_argument("--mp4", action="store_true", help="remux to mp4 when ffmpeg exists")
    p_download.set_defaults(func=download_command)

    p_sd_info = sub.add_parser("sd-info", help="show local SD/P2P cache information")
    p_sd_info.add_argument("--did", required=True)
    p_sd_info.set_defaults(func=print_sd_info)

    p_sd_list = sub.add_parser("sd-list", help="list SD videos cached by Xiaomi Home")
    p_sd_list.add_argument("--did", required=True)
    p_sd_list.add_argument("--start", help="ISO time or unix timestamp")
    p_sd_list.add_argument("--end", help="ISO time or unix timestamp")
    p_sd_list.set_defaults(func=print_sd_cached_list)

    p_sd_download = sub.add_parser(
        "sd-download",
        help="export SD videos cached by Xiaomi Home after RDT download",
    )
    p_sd_download.add_argument("--did", required=True)
    p_sd_download.add_argument("--file-id")
    p_sd_download.add_argument("--start", help="ISO time or unix timestamp")
    p_sd_download.add_argument("--end", help="ISO time or unix timestamp")
    p_sd_download.add_argument("--out", default="mijia-sd-downloads")
    p_sd_download.add_argument("--overwrite", action="store_true")
    p_sd_download.set_defaults(func=sd_download_command)

    p_sd_rdt = sub.add_parser("sd-rdt-command", help="encode verified SD RDT command frame")
    p_sd_rdt.add_argument("--rdt-command", type=int, required=True, choices=[1, 5, 6, 16])
    p_sd_rdt.add_argument("--timestamp", help="required for commands 1, 5, and 16")
    p_sd_rdt.add_argument("--channel", type=int, default=0)
    p_sd_rdt.set_defaults(func=print_sd_rdt_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "download" and not args.file_id and (not args.start or not args.end):
        parser.error("download requires --file-id or both --start and --end")
    if args.command == "sd-download" and not args.file_id and (not args.start or not args.end):
        parser.error("sd-download requires --file-id or both --start and --end")
    if args.command == "sd-rdt-command" and args.rdt_command in {1, 5, 16} and not args.timestamp:
        parser.error("sd-rdt-command --rdt-command 1/5/16 requires --timestamp")
    try:
        return args.func(args)
    except MijiaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
