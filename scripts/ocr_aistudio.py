#!/usr/bin/env python3
"""Submit one local image/PDF to the documented AI Studio asynchronous OCR API.

Python 3.9+, standard library only. Credentials are read only from
AISTUDIO_ACCESS_TOKEN. This program does not grade answers or verify OCR text.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
MODELS = ("PP-OCRv5", "PP-StructureV3", "PaddleOCR-VL", "PaddleOCR-VL-1.5")
MAX_INPUT_BYTES = 50 * 1024 * 1024  # Local resource guard, not a service quota.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


class OCRError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a token through a redirect, including same-host redirects.
        raise OCRError("redirect_refused", "服务返回重定向，已停止；没有转发鉴权令牌。请核对官方 API 文档。")


def clean(value, token):
    """Keep a server echo from exposing the supplied credential in any output."""
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]") if token else value
    if isinstance(value, list):
        return [clean(v, token) for v in value]
    if isinstance(value, dict):
        return {clean(k, token): clean(v, token) for k, v in value.items()}
    return value


def write_result(path, result, token):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".ocr-", suffix=".json", delete=False) as f:
            name = f.name
            json.dump(clean(result, token), f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def error_from_response(status, code=None):
    if status == 401 or code == 401:
        return OCRError("authentication", "AI Studio 返回 401：令牌无效或已过期。检查 AISTUDIO_ACCESS_TOKEN，或使用本地 OCR。")
    if code in (12001, 12002, 12003) or status == 429:
        return OCRError("quota_or_rate_limit", "AI Studio 配额或频率受限；不要重复提交文件。等待额度恢复，或改用本地 OCR。")
    if status == 403 or code == 403:
        return OCRError("forbidden", "AI Studio 返回 403：无访问权限或配额受限。检查账户权限/配额，或使用本地 OCR。")
    return OCRError("service_error", "AI Studio 请求失败（HTTP %s，业务码 %s）。核对文件格式、页数、模型及官方错误码；未自动重试提交。" % (status, code))


def request_bytes(url, *, method="GET", body=None, headers=None, timeout=30,
                  limit=MAX_RESPONSE_BYTES):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise OCRError("response_too_large", "响应超过本脚本的大小上限；已停止并保留任务编号。")
            return data
    except urllib.error.HTTPError as exc:
        code = None
        try:
            obj = json.loads(exc.read(65536))
            code = obj.get("code", obj.get("errorCode")) if isinstance(obj, dict) else None
        except (ValueError, OSError):
            pass
        if isinstance(code, str) and code.isdigit():
            code = int(code)
        raise error_from_response(exc.code, code) from None
    except (TimeoutError, socket.timeout):
        raise OCRError("network_timeout", "网络请求超时。提交阶段超时不能确定服务器是否已接收；禁止自动重发 POST。已有任务编号时仅恢复查询。") from None
    except urllib.error.URLError:
        raise OCRError("network_error", "无法连接官方 OCR 服务。没有自动重新提交；可检查网络或使用本地 OCR。") from None


def api_request(url, token, timeout, *, body=None, content_type=None):
    # Do not allow a caller or returned job ID to change the credential destination.
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "paddleocr.aistudio-app.com"
            or not parts.path.startswith("/api/v2/ocr/jobs")):
        raise OCRError("invalid_api_url", "鉴权请求必须发送至脚本固定的官方 OCR 端点。")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    data = request_bytes(url, method="POST" if body is not None else "GET", body=body,
                         headers=headers, timeout=timeout)
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError):
        raise OCRError("invalid_json", "官方服务未返回有效 JSON；未据此判定 OCR 成功。") from None
    if not isinstance(obj, dict) or obj.get("code") != 0 or not isinstance(obj.get("data"), dict):
        code = obj.get("code") if isinstance(obj, dict) else None
        raise error_from_response(200, code)
    return obj["data"]


def multipart(file_bytes, suffix, model, preprocess):
    boundary = "ocr_" + uuid.uuid4().hex
    optional = {"useDocOrientationClassify": preprocess, "useDocUnwarping": preprocess}
    if model == "PP-OCRv5":
        optional["useTextlineOrientation"] = preprocess
    parts = []
    for key, val in (("model", model), ("optionalPayload", json.dumps(optional))):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" %
                      (boundary, key, val)).encode("utf-8"))
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"upload%s\"\r\n"
                  "Content-Type: application/octet-stream\r\n\r\n" % (boundary, suffix)).encode("ascii"))
    parts.extend((file_bytes, ("\r\n--%s--\r\n" % boundary).encode("ascii")))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def validate_job_id(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", job_id):
        raise OCRError("invalid_job_id", "服务未返回有效任务编号；不重发 POST。请检查账户任务记录。")
    return job_id


def poll_job(job_id, token, request_timeout, poll_timeout, interval,
             api=api_request, now=time.monotonic, sleep=time.sleep):
    deadline = now() + poll_timeout
    while True:
        remaining = deadline - now()
        if remaining <= 0:
            raise OCRError("poll_timeout", "轮询超时；原任务可能仍在处理。使用 --job-id 恢复原任务查询，禁止重新提交同一文件。")
        data = api(JOB_URL + "/" + validate_job_id(job_id), token,
                   min(request_timeout, remaining))
        state = data.get("state")
        if state == "done":
            urls = data.get("resultUrl")
            if not isinstance(urls, dict) or not isinstance(urls.get("jsonUrl"), str) or not urls["jsonUrl"]:
                raise OCRError("empty_result", "任务标记完成但缺少 JSON 结果地址；不视为成功。")
            return data
        if state == "failed":
            raise OCRError("job_failed", "官方 OCR 任务处理失败；请检查文件格式/内容或使用本地 OCR。")
        if state not in ("pending", "running"):
            raise OCRError("unknown_state", "服务返回未知任务状态；已停止并保留任务编号。")
        remaining = deadline - now()
        if remaining > 0:
            sleep(min(interval, remaining))


def download_results(url, timeout):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    allowed = (host == "paddleocr.aistudio-app.com" or host.endswith(".bcebos.com")
               or host.endswith(".aistudio-app.com"))
    try:
        valid_port = parts.port in (None, 443)
    except ValueError:
        valid_port = False
    if parts.scheme != "https" or not allowed or parts.username or parts.password or not valid_port:
        raise OCRError("result_url_refused", "结果地址不属于已核验的 HTTPS 官方 OCR/BOS 域名。请核对官方文档后更新白名单，勿向新域名发送令牌。")
    # Signed result URL: intentionally no Authorization header and no redirects.
    raw = request_bytes(url, timeout=timeout)
    try:
        contents = raw.decode("utf-8-sig")
        records = [json.loads(line) for line in contents.splitlines() if line.strip()]
    except (ValueError, UnicodeError):
        raise OCRError("invalid_result", "结果不是官方约定的 UTF-8 JSONL；保留任务编号以便核对。") from None
    if not records or any(not isinstance(r, dict) for r in records):
        raise OCRError("empty_result", "没有获得有效的逐页 OCR 结果；不视为成功。")
    return records


def extract_pages(records):
    pages = []
    for record_number, record in enumerate(records, 1):
        result = record.get("result")
        if not isinstance(result, dict):
            raise OCRError("invalid_result", "JSONL 记录缺少 result 对象；不可静默漏页。")
        items = result.get("ocrResults")
        layout = items is None
        if layout:
            items = result.get("layoutParsingResults")
        if not isinstance(items, list) or not items:
            raise OCRError("empty_result", "某条记录缺少逐页结果；不可静默漏页。")
        for item in items:
            if not isinstance(item, dict):
                raise OCRError("invalid_result", "逐页结果格式无效。")
            lines = []
            if layout:
                markdown = item.get("markdown", {})
                text = markdown.get("text") if isinstance(markdown, dict) else None
                if not isinstance(text, str):
                    raise OCRError("invalid_result", "版面解析结果缺少 markdown.text。")
            else:
                pruned = item.get("prunedResult")
                if not isinstance(pruned, dict) or not isinstance(pruned.get("rec_texts"), list):
                    raise OCRError("invalid_result", "文字识别结果缺少 prunedResult.rec_texts。")
                texts = pruned["rec_texts"]
                if any(not isinstance(t, str) for t in texts):
                    raise OCRError("invalid_result", "识别文字包含非文本值。")
                scores = pruned.get("rec_scores", [])
                polygons = pruned.get("rec_polys", [])
                for i, line in enumerate(texts):
                    score = scores[i] if isinstance(scores, list) and i < len(scores) else None
                    if not isinstance(score, (int, float)) or not math.isfinite(score):
                        score = None
                    lines.append({"text": line, "confidence": score,
                                  "polygon": polygons[i] if isinstance(polygons, list) and i < len(polygons) else None})
                text = "\n".join(texts)
            pages.append({"page": len(pages) + 1, "source_record": record_number,
                          "text": text, "lines": lines})
    return pages


def positive_number(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise argparse.ArgumentTypeError("必须为正数")
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="单个本地图片或 PDF；不接受远程输入 URL")
    parser.add_argument("--output", required=True, type=Path, help="新建 JSON 输出路径；为防止重复提交，不覆盖已有结果")
    parser.add_argument("--model", choices=MODELS, default="PP-OCRv5")
    parser.add_argument("--preprocess", action="store_true", help="照片弯曲或旋转时启用方向/展平处理；清晰截图默认关闭")
    parser.add_argument("--request-timeout", type=positive_number, default=30)
    parser.add_argument("--poll-timeout", type=positive_number, default=180)
    parser.add_argument("--poll-interval", type=positive_number, default=5)
    parser.add_argument("--job-id", help="恢复已有任务，只 GET 查询，绝不 POST；仍须提供原 INPUT 与新的 OUTPUT 路径")
    args = parser.parse_args(argv)
    token = os.environ.get("AISTUDIO_ACCESS_TOKEN", "").strip()
    output = args.output.expanduser().resolve()
    result = {"schema_version": 1, "engine": "baidu-aistudio", "status": "error",
              "model": args.model, "input": str(args.input.expanduser().resolve()),
              "job_id": None, "text": "", "pages": [], "raw_results": [],
              "warnings": ["机器 OCR 尚未人工复核；罪名、否定词、人物、数字及作答编号必须回看原图。"]}
    if output.exists():
        print("输出文件已存在；请查看其中 job_id 并恢复查询，或指定新的输出路径。", file=sys.stderr)
        return 2
    try:
        if not token:
            raise OCRError("missing_token", "未设置 AISTUDIO_ACCESS_TOKEN。可配置环境变量或使用本地 OCR；不向脚本、文档或命令行参数写入令牌。")
        path = args.input.expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            raise OCRError("invalid_input", "INPUT 必须是一个现有本地 PDF 或常见图片文件。")
        size = path.stat().st_size
        if not 0 < size <= MAX_INPUT_BYTES:
            raise OCRError("invalid_input", "本脚本接受 1 字节至 50 MiB 的文件；这是本地资源上限，服务配额可能更低。")
        data = path.read_bytes()
        result["input_sha256"] = hashlib.sha256(data).hexdigest()
        result["input_bytes"] = len(data)
        request_timeout = min(args.request_timeout, 60)
        if args.job_id:
            job_id = validate_job_id(args.job_id)
            result["warnings"].append("恢复任务时请核对 INPUT 与原任务文件一致；本脚本无法从 job_id 独立验证二者对应关系。")
        else:
            body, content_type = multipart(data, path.suffix.lower(), args.model, args.preprocess)
            submission = api_request(JOB_URL, token, request_timeout, body=body,
                                     content_type=content_type)  # Exactly one POST; never retried.
            job_id = validate_job_id(submission.get("jobId"))
        result.update(job_id=job_id, status="submitted")
        write_result(output, result, token)  # Checkpoint before waiting; preserves ID on interruption.
        completed = poll_job(job_id, token, request_timeout, args.poll_timeout,
                             max(1, min(args.poll_interval, 60)))
        records = download_results(completed["resultUrl"]["jsonUrl"], request_timeout)
        result["raw_results"] = records
        pages = extract_pages(records)
        result.update(pages=pages, text="\n\n".join(p["text"] for p in pages))
        if not result["text"].strip():
            raise OCRError("no_text_detected", "OCR 结果没有可读文字；原始结果已保存。请回看原图或切换 OCR，不能按空白答卷评分。")
        empty = [p["page"] for p in pages if not p["text"].strip()]
        if empty:
            result["warnings"].append("以下页未识别出文字，须核对是否空白或漏识别：" + ", ".join(map(str, empty)))
        result.update(status="completed", extract_progress=completed.get("extractProgress", {}))
        progress = result["extract_progress"]
        if isinstance(progress, dict):
            for key in ("totalPages", "extractedPages"):
                count = progress.get(key)
                if str(count).isdigit() and int(count) != len(pages):
                    raise OCRError("page_count_mismatch", "服务声明页数与下载的逐页结果数量不一致；已保留 OCR 结果，须核对全部页后才能评分。")
        write_result(output, result, token)
        print("OCR 结果已保存：%s（%s 页；须回看原图复核）" % (output, len(pages)))
        return 0
    except OCRError as exc:
        result.update(status="error", error={"kind": exc.kind, "message": str(exc)})
        try:
            write_result(output, result, token)
        except OSError:
            pass
        print(clean(str(exc), token), file=sys.stderr)
        if result.get("job_id"):
            print("已保留原任务编号：" + result["job_id"], file=sys.stderr)
        return 3
    except (OSError, ValueError):
        # Avoid exception messages that might contain signed URLs or credentials.
        print("本地文件读取/写入或参数处理失败；没有自动重新提交。请检查路径与权限。", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
