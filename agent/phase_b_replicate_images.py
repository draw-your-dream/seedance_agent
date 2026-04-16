from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_DEPLOYMENT = "picaa/qwen-image-distilled-test-1109"
REPLICATE_API_BASE = "https://api.replicate.com/v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def replicate_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Replicate HTTP {exc.code}: {error_body}") from exc
    return json.loads(body)


def create_prediction(
    deployment: str,
    token: str,
    prediction_input: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    owner, name = deployment.split("/", 1)
    url = f"{REPLICATE_API_BASE}/deployments/{owner}/{name}/predictions"
    return replicate_request("POST", url, token, {"input": prediction_input}, timeout=timeout)


def wait_prediction(prediction: dict[str, Any], token: str, poll_interval: float, timeout: int) -> dict[str, Any]:
    get_url = prediction.get("urls", {}).get("get")
    if not get_url:
        return prediction

    deadline = time.monotonic() + timeout
    current = prediction
    while current.get("status") not in {"succeeded", "failed", "canceled"}:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Prediction timed out: {current.get('id')}")
        time.sleep(poll_interval)
        for attempt in range(1, 6):
            try:
                current = replicate_request("GET", get_url, token, timeout=120)
                break
            except TimeoutError:
                if attempt == 5:
                    raise
                time.sleep(poll_interval * attempt)
            except urllib.error.URLError:
                if attempt == 5:
                    raise
                time.sleep(poll_interval * attempt)
    return current


def build_input(row: dict[str, Any], args: argparse.Namespace, index: int) -> dict[str, Any]:
    prompt = str(row.get(args.prompt_field, "")).strip()
    if not prompt:
        raise ValueError(f"row missing prompt field: {args.prompt_field}")

    payload: dict[str, Any] = {
        "prompt": prompt,
        "width": args.width,
        "height": args.height,
        "num_inference_steps": args.num_inference_steps,
        "true_cfg_scale": args.true_cfg_scale,
        "num_images_per_prompt": args.num_images_per_prompt,
        "negative_prompt": args.negative_prompt,
    }
    if args.input_image:
        payload["input_images"] = args.input_image
    elif args.empty_input_images:
        payload["input_images"] = []
    if args.segment_prompt:
        payload["segment_prompt"] = args.segment_prompt
    if args.seed is not None:
        payload["seed"] = args.seed + index

    return {key: value for key, value in payload.items() if value not in (None, "")}


def iter_output_urls(output: Any) -> list[str]:
    if isinstance(output, str):
        return [output]
    if isinstance(output, list):
        return [item for item in output if isinstance(item, str)]
    return []


def download_outputs(row: dict[str, Any], image_dir: Path, index: int) -> list[str]:
    output_urls = iter_output_urls(row.get("output"))
    saved: list[str] = []
    image_dir.mkdir(parents=True, exist_ok=True)
    for output_index, url in enumerate(output_urls, start=1):
        parsed = urllib.parse.urlparse(url)
        suffix = Path(parsed.path).suffix or ".png"
        target = image_dir / f"{index:05d}_{output_index:02d}{suffix}"
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
        saved.append(str(target))
    return saved


def run(args: argparse.Namespace) -> Path:
    token = os.environ.get("REPLICATE_API_TOKEN")
    if not token:
        raise RuntimeError("Missing REPLICATE_API_TOKEN environment variable.")

    rows = read_jsonl(args.input_jsonl)
    selected = rows[args.start : args.start + args.limit if args.limit else None]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = args.output_dir / "images"
    results_path = args.output_dir / "replicate_predictions.jsonl"

    results: list[dict[str, Any]] = read_jsonl(results_path) if results_path.exists() else []
    finished_indices = {
        int(row["index"])
        for row in results
        if row.get("status") in {"succeeded", "failed", "canceled"} and "index" in row
    }
    for offset, row in enumerate(selected):
        index = args.start + offset
        if index in finished_indices:
            continue
        prediction_input = build_input(row, args, index)
        result: dict[str, Any] = {
            "index": index,
            "event_id": row.get("event_id"),
            "context_id": row.get("context_id"),
            "title": row.get("title"),
            "input": prediction_input,
        }

        if args.dry_run:
            result["status"] = "dry_run"
            results.append(result)
            write_jsonl(results_path, results)
            continue

        prediction = create_prediction(args.deployment, token, prediction_input, timeout=args.request_timeout)
        result.update(
            {
                "prediction_id": prediction.get("id"),
                "status": prediction.get("status"),
                "output": prediction.get("output"),
                "error": prediction.get("error"),
            }
        )
        results.append(result)
        write_jsonl(results_path, results)
        prediction = wait_prediction(prediction, token, args.poll_interval, args.prediction_timeout)
        results[-1].update(
            {
                "prediction_id": prediction.get("id"),
                "status": prediction.get("status"),
                "output": prediction.get("output"),
                "error": prediction.get("error"),
                "metrics": prediction.get("metrics"),
            }
        )
        if args.download and prediction.get("status") == "succeeded":
            results[-1]["downloaded_files"] = download_outputs(results[-1], image_dir, index + 1)
        write_jsonl(results_path, results)

    write_jsonl(results_path, results)
    return results_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase B: use Replicate to turn Phase A prompts into images.")
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=Path(__file__).parent / "outputs" / "gemini_25_flash_50_b25" / "phase_a_text_to_image_prompts.jsonl",
        help="Path to phase_a_text_to_image_prompts.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "outputs" / "replicate_images")
    parser.add_argument("--deployment", default=DEFAULT_DEPLOYMENT, help="Replicate deployment in owner/name form")
    parser.add_argument("--prompt-field", default="text_to_image_prompt")
    parser.add_argument("--input-image", action="append", help="Input image URL for image-edit deployments.")
    parser.add_argument(
        "--empty-input-images",
        action="store_true",
        help="Send input_images=[] for deployments that require the key but can run text-to-image without reference images.",
    )
    parser.add_argument("--segment-prompt", default="")
    parser.add_argument("--negative-prompt", default=" ")
    parser.add_argument("--width", type=int, default=720)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--num-inference-steps", type=int, default=4)
    parser.add_argument("--true-cfg-scale", type=float, default=1.0)
    parser.add_argument("--num-images-per-prompt", type=int, default=1)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--prediction-timeout", type=int, default=900)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    path = run(parse_args())
    print(json.dumps({"predictions_jsonl": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
