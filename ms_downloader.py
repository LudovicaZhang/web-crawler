from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

CATEGORIES = {
    "chinos": "https://www.marksandspencer.com/l/men/mens-trousers/fs5/chinos",
    "holiday_shop": "https://www.marksandspencer.com/l/men/mens-holiday-shop",
    "hoodies_and_sweatshirts": "https://www.marksandspencer.com/l/men/mens-hoodies-and-sweatshirts",
    "jackets_and_coats": "https://www.marksandspencer.com/l/men/mens-coats-and-jackets",
    "jeans": "https://www.marksandspencer.com/l/men/mens-jeans",
    "joggers": "https://www.marksandspencer.com/l/men/mens-joggers",
    "knitwear": "https://www.marksandspencer.com/l/men/mens-knitwear",
    "polo_shirts": "https://www.marksandspencer.com/l/men/mens-tops/mens-polo-shirts",
    "shirts": "https://www.marksandspencer.com/l/men/mens-shirts",
    "shorts": "https://www.marksandspencer.com/l/men/mens-shorts",
    "sportswear": "https://www.marksandspencer.com/l/men/mens-sportswear",
    "swimwear": "https://www.marksandspencer.com/l/men/mens-swimwear",
    "tops": "https://www.marksandspencer.com/l/men/mens-tops",
    "trousers": "https://www.marksandspencer.com/l/men/mens-trousers",
    "t_shirts": "https://www.marksandspencer.com/l/men/mens-tops/mens-tshirts",
}

NEXT_PAGE_PATTERN = re.compile(r'href="([^"]*page=(\d+)[^"]*)"')
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
PRODUCT_CODE_PATTERN = re.compile(r"Product code:\s*(?:<!-- -->)?([A-Z0-9/]+)")
JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL,
)


@dataclass
class ProductMetadata:
    colour: str
    price: str
    composition: str
    product_code: str


INFO_FONT_SIZE = 280
INFO_MIN_FONT_SIZE = 56
INFO_LINE_GAP = 60
INFO_PADDING_X = 120
INFO_PADDING_Y = 100
MAX_CANVAS_PIXELS = 60_000_000
MIN_RENDER_SCALE = 0.35
MIN_OUTPUT_FILE_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_FILE_BYTES = 30 * 1024 * 1024
TARGET_OUTPUT_FILE_BYTES = 25 * 1024 * 1024
MIN_OUTPUT_SCALE = 0.45
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0
HTML_CACHE_TTL_SECONDS = 24 * 60 * 60
LOCAL_OUTPUT_ROOT = Path(r"C:\Users\Administrator\Desktop\M&S")
SHARED_OUTPUT_ROOT = Path(r"\\192.168.1.18\跨部门共享\设计图片下载\官网下图\男装\M&S")


@dataclass
class RunContext:
    request_manager: "RequestManager"
    cache_root: Path
    progress_root: Path


class RequestManager:
    def __init__(self, delay_seconds: float = REQUEST_DELAY_SECONDS):
        self.delay_seconds = delay_seconds
        self._last_request_at = 0.0

    def fetch_text(self, url: str, cache_path: Path | None = None, ttl_seconds: int | None = None) -> str:
        payload = self.fetch_bytes(url, timeout=30, cache_path=cache_path, ttl_seconds=ttl_seconds)
        return payload.decode("utf-8", errors="ignore")

    def fetch_bytes(
        self,
        url: str,
        *,
        timeout: int,
        cache_path: Path | None = None,
        ttl_seconds: int | None = None,
    ) -> bytes:
        if cache_path and is_cache_fresh(cache_path, ttl_seconds):
            return cache_path.read_bytes()

        headers = dict(BASE_HEADERS)
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._wait_for_rate_limit()
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = response.read()
                if cache_path:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(payload)
                return payload
            except urllib.error.HTTPError as exc:
                if not is_retryable_http_error(exc.code) or attempt == MAX_RETRIES:
                    raise
                self._sleep_before_retry(attempt, exc.headers.get("Retry-After"))
            except urllib.error.URLError:
                if attempt == MAX_RETRIES:
                    raise
                self._sleep_before_retry(attempt)

        raise RuntimeError(f"Failed to fetch after retries: {url}")

    def _wait_for_rate_limit(self) -> None:
        now = time.monotonic()
        remaining = self.delay_seconds - (now - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _sleep_before_retry(self, attempt: int, retry_after: str | None = None) -> None:
        if retry_after and retry_after.isdigit():
            time.sleep(max(float(retry_after), self.delay_seconds))
            return
        time.sleep(max(self.delay_seconds, BACKOFF_BASE_SECONDS ** (attempt - 1)))


def is_retryable_http_error(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def is_cache_fresh(path: Path, ttl_seconds: int | None) -> bool:
    if not path.exists():
        return False
    if ttl_seconds is None:
        return True
    return (time.time() - path.stat().st_mtime) <= ttl_seconds


def url_cache_path(cache_root: Path, namespace: str, url: str, suffix: str) -> Path:
    digest = sha256(url.encode("utf-8")).hexdigest()
    return cache_root / namespace / f"{digest}{suffix}"


def url_temp_path(temp_root: Path, url: str, suffix: str) -> Path:
    digest = sha256(url.encode("utf-8")).hexdigest()
    return temp_root / "source_images" / f"{digest}{suffix}"


def load_progress(progress_path: Path) -> dict:
    if not progress_path.exists():
        return {"completed": []}
    try:
        return json.loads(progress_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"completed": []}


def save_progress(progress_path: Path, progress: dict) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def sanitize_filename(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .")


def find_next_page_url(html: str, current_url: str, current_page: int) -> str | None:
    next_page = current_page + 1
    for href, page in NEXT_PAGE_PATTERN.findall(html):
        if int(page) != next_page:
            continue
        return urllib.parse.urljoin(current_url, href.replace("&amp;", "&"))
    return None


def extract_next_data(html: str) -> dict:
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        raise ValueError("Could not locate __NEXT_DATA__ payload")
    return json.loads(match.group(1))


def extract_product_details(html: str) -> dict:
    return extract_next_data(html)["props"]["pageProps"]["productDetails"]


def extract_product_urls(html: str, current_url: str) -> list[str]:
    data = extract_next_data(html)
    products = (
        data.get("props", {})
        .get("pageProps", {})
        .get("serverSideGqlResponseFed", {})
        .get("productPageData", {})
        .get("search", {})
        .get("results", {})
        .get("products", [])
    )

    urls: list[str] = []
    seen: set[str] = set()
    for product in products:
        seo_path = product.get("seoPath")
        if not seo_path:
            continue
        absolute_url = urllib.parse.urljoin(current_url, seo_path)
        normalized = normalize_product_url(absolute_url)
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(absolute_url)
    return urls


def normalize_product_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept_query = [(key, value) for key, value in query if key == "color"]
    normalized_query = urllib.parse.urlencode(kept_query)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, normalized_query, "")
    )


def extract_product_code(html: str) -> str:
    match = PRODUCT_CODE_PATTERN.search(html)
    if not match:
        raise ValueError("Product code not found on PDP")
    return match.group(1)


def normalize_colour_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def find_selected_variant(product_details: dict, product_url: str) -> dict:
    variants = product_details.get("variants", [])
    if not variants:
        raise ValueError("No variants found on PDP")

    colour_value = urllib.parse.parse_qs(urllib.parse.urlsplit(product_url).query).get(
        "color", [None]
    )[0]
    if not colour_value:
        return variants[0]

    target = normalize_colour_key(colour_value)
    for variant in variants:
        for candidate in (
            variant.get("colour"),
            variant.get("exactColourForBeauty"),
        ):
            if candidate and normalize_colour_key(candidate) == target:
                return variant
    return variants[0]


def format_price(currency_prefix: str, value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{currency_prefix}{value:.2f}"
    return f"{currency_prefix}{int(value)}"


def format_colour(value: str) -> str:
    return value.title()


def extract_product_metadata(html: str, product_url: str) -> ProductMetadata:
    product_details = extract_product_details(html)
    attributes = product_details.get("attributes", {})
    selected_variant = find_selected_variant(product_details, product_url)
    first_sku = selected_variant.get("skus", [{}])[0]
    price = first_sku.get("price", {})

    product_code = attributes.get("strokeId") or extract_product_code(html)
    composition = attributes.get("compositionList") or attributes.get("ingredientsListing") or ""
    if not composition:
        raise ValueError("Composition not found on PDP")

    return ProductMetadata(
        colour=format_colour(selected_variant.get("colour") or ""),
        price=format_price(price.get("currencyPrefix", "£"), price.get("currentPrice", 0)),
        composition=composition.strip(),
        product_code=product_code.strip(),
    )


def extract_large_image_urls(html: str) -> list[str]:
    for match in JSON_LD_PATTERN.finditer(html):
        raw_payload = match.group(1).strip()
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        if payload.get("@type") != "Product":
            continue
        images = payload.get("image", [])
        if not isinstance(images, list):
            continue

        urls: list[str] = []
        seen: set[str] = set()
        for image_url in images:
            if not isinstance(image_url, str):
                continue
            large_url = upgrade_image_url(image_url)
            if large_url in seen:
                continue
            seen.add(large_url)
            urls.append(large_url)
        if urls:
            return urls

    raise ValueError("Product image list not found on PDP")


def upgrade_image_url(image_url: str) -> str:
    return image_url.replace("/images/q_auto,f_auto/", "/images/w_2560,q_auto,f_auto/")


def safe_extension(image_url: str, content_type: str | None) -> str:
    path = urllib.parse.urlparse(image_url).path
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        return ext
    if content_type:
        lowered = content_type.lower()
        if "png" in lowered:
            return ".png"
        if "webp" in lowered:
            return ".webp"
    return ".jpg"


def download_image(image_url: str, temp_root: Path, request_manager: RequestManager) -> Path:
    guessed_extension = safe_extension(image_url, None)
    temp_path = url_temp_path(temp_root, image_url, guessed_extension)
    if temp_path.exists():
        return temp_path

    payload = request_manager.fetch_bytes(image_url, timeout=60)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(payload)
    return temp_path


def copy_file_if_needed(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        source_size = source_path.stat().st_size
        destination_size = destination_path.stat().st_size
        if source_size == destination_size:
            return
    last_error: OSError | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            shutil.copy2(source_path, destination_path)
            return
        except OSError as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            time.sleep(max(1.0, BACKOFF_BASE_SECONDS ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def sync_local_outputs_to_shared(local_root: Path, shared_root: Path) -> int:
    moved_count = 0
    if not local_root.exists():
        return moved_count

    for category_name in CATEGORIES:
        local_category_dir = local_root / category_name
        if not local_category_dir.exists():
            continue

        shared_category_dir = shared_root / category_name
        for local_file in local_category_dir.glob("*.png"):
            shared_file = shared_category_dir / local_file.name
            copy_file_if_needed(local_file, shared_file)
            if shared_file.exists() and shared_file.stat().st_size == local_file.stat().st_size:
                local_file.unlink()
                moved_count += 1

    for category_name in CATEGORIES:
        local_category_dir = local_root / category_name
        if local_category_dir.exists() and not any(local_category_dir.iterdir()):
            local_category_dir.rmdir()
    if local_root.exists() and not any(local_root.iterdir()):
        local_root.rmdir()
    return moved_count


def transfer_local_file_to_shared(local_file: Path, shared_file: Path) -> bool:
    if not local_file.exists():
        return False

    copy_file_if_needed(local_file, shared_file)
    if shared_file.exists() and shared_file.stat().st_size == local_file.stat().st_size:
        local_file.unlink()
        return True
    return False


def build_metadata_lines(metadata: ProductMetadata) -> list[str]:
    return [
        f"colour: {metadata.colour}",
        f"price: {metadata.price}",
        f"Composition: {metadata.composition}",
        f"product code: {metadata.product_code}",
    ]


def quote_powershell_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_font(size: int):
    from PIL import ImageFont  # type: ignore

    font_candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for font_path in font_candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def wrap_text_to_width(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = measure_text_bbox(draw, candidate, font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_wrapped_text_layout(draw, info_lines: list[str], font, max_text_width: int):
    wrapped_lines: list[str] = []
    for line in info_lines:
        wrapped_lines.extend(wrap_text_to_width(draw, line, font, max_text_width))

    line_heights = []
    for line in wrapped_lines:
        bbox = measure_text_bbox(draw, line, font)
        line_heights.append(bbox[3] - bbox[1])
    return wrapped_lines, line_heights


def measure_text_bbox(draw, text: str, font):
    # Pillow versions in this environment do not expose ImageDraw.textbbox().
    if hasattr(draw, "textbbox"):
        return draw.textbbox((0, 0), text, font=font)
    width, height = draw.textsize(text, font=font)
    return (0, 0, width, height)


def save_stitched_image(image, output_path: Path) -> None:
    suffix = output_path.suffix.lower()
    tried_scales: dict[float, int] = {}

    def save_at_scale(scale: float) -> int:
        rounded_scale = round(scale, 4)
        if rounded_scale in tried_scales:
            return tried_scales[rounded_scale]

        candidate = image
        try:
            if rounded_scale < 1.0:
                target_width = max(1, int(round(image.width * rounded_scale)))
                target_height = max(1, int(round(image.height * rounded_scale)))
                candidate = image.resize((target_width, target_height), get_resample_filter())

            if suffix == ".png":
                candidate.save(output_path, format="PNG", compress_level=0)
            else:
                candidate.save(output_path, format="JPEG", quality=100, subsampling=0)
            size = output_path.stat().st_size
            tried_scales[rounded_scale] = size
            return size
        finally:
            if candidate is not image:
                candidate.close()

    size_at_full = save_at_scale(1.0)
    if MIN_OUTPUT_FILE_BYTES <= size_at_full <= MAX_OUTPUT_FILE_BYTES:
        return
    if size_at_full < MIN_OUTPUT_FILE_BYTES:
        print(
            f"  warning {output_path.name} below 20MB at full size "
            f"({size_at_full / (1024 * 1024):.1f}MB)"
        )
        return

    size_at_min = save_at_scale(MIN_OUTPUT_SCALE)
    if size_at_min > MAX_OUTPUT_FILE_BYTES:
        print(
            f"  warning {output_path.name} still larger than 30MB "
            f"({size_at_min / (1024 * 1024):.1f}MB) at minimum scale"
        )
        return

    lower = MIN_OUTPUT_SCALE
    upper = 1.0
    best_in_range: tuple[float, int] | None = None
    best_under_limit: tuple[float, int] | None = None

    for _ in range(14):
        mid = round((lower + upper) / 2, 4)
        size = save_at_scale(mid)

        if size <= MAX_OUTPUT_FILE_BYTES:
            if best_under_limit is None or size > best_under_limit[1]:
                best_under_limit = (mid, size)
        if MIN_OUTPUT_FILE_BYTES <= size <= MAX_OUTPUT_FILE_BYTES:
            if (
                best_in_range is None
                or abs(size - TARGET_OUTPUT_FILE_BYTES) < abs(best_in_range[1] - TARGET_OUTPUT_FILE_BYTES)
            ):
                best_in_range = (mid, size)

        if size > MAX_OUTPUT_FILE_BYTES:
            upper = mid
        elif size < MIN_OUTPUT_FILE_BYTES:
            lower = mid
        else:
            if size > TARGET_OUTPUT_FILE_BYTES:
                upper = mid
            else:
                lower = mid

    chosen_scale = 1.0
    if best_in_range is not None:
        chosen_scale = best_in_range[0]
    elif best_under_limit is not None:
        chosen_scale = best_under_limit[0]

    save_at_scale(chosen_scale)


def choose_render_scale(total_width: int, image_height: int, info_lines: list[str]) -> float:
    from PIL import Image, ImageDraw  # type: ignore

    probe = Image.new("RGB", (10, 10), color="white")
    try:
        draw = ImageDraw.Draw(probe)
        scale = 1.0
        while scale >= MIN_RENDER_SCALE:
            scaled_width = max(1, int(round(total_width * scale)))
            scaled_height = max(1, int(round(image_height * scale)))
            max_text_width = max(scaled_width - INFO_PADDING_X * 2, 200)
            max_info_height = max(int(scaled_height * 1.2), 1200)

            min_font_size = max(18, int(round(INFO_MIN_FONT_SIZE * scale)))
            selected_font_size = None
            info_height = 0
            for font_size in range(max(int(round(INFO_FONT_SIZE * scale)), min_font_size), min_font_size - 1, -6):
                font = load_font(font_size)
                wrapped_lines, line_heights = build_wrapped_text_layout(
                    draw, info_lines, font, max_text_width
                )
                info_height = (
                    INFO_PADDING_Y * 2
                    + sum(line_heights)
                    + INFO_LINE_GAP * (len(wrapped_lines) - 1)
                )
                selected_font_size = font_size
                if info_height <= max_info_height:
                    break

            if selected_font_size is None:
                raise RuntimeError("Unable to prepare text layout")

            total_pixels = scaled_width * (scaled_height + info_height)
            if total_pixels <= MAX_CANVAS_PIXELS:
                return scale

            next_scale = scale * ((MAX_CANVAS_PIXELS / total_pixels) ** 0.5) * 0.98
            if next_scale >= scale:
                next_scale = scale - 0.05
            scale = round(next_scale, 3)

        return MIN_RENDER_SCALE
    finally:
        probe.close()


def build_text_layout_for_canvas(canvas_width: int, image_height: int, metadata: ProductMetadata):
    from PIL import Image, ImageDraw  # type: ignore

    info_lines = build_metadata_lines(metadata)
    probe = Image.new("RGB", (10, 10), color="white")
    try:
        draw = ImageDraw.Draw(probe)
        max_text_width = max(canvas_width - INFO_PADDING_X * 2, 200)
        max_info_height = max(int(image_height * 1.2), 1200)

        min_font_size = max(18, int(round(INFO_MIN_FONT_SIZE * min(canvas_width / 2560, 1.0))))
        selected_font = None
        wrapped_lines: list[str] = []
        line_heights: list[int] = []
        info_height = 0
        for font_size in range(INFO_FONT_SIZE, min_font_size - 1, -8):
            font = load_font(font_size)
            wrapped_lines, line_heights = build_wrapped_text_layout(
                draw, info_lines, font, max_text_width
            )
            info_height = (
                INFO_PADDING_Y * 2
                + sum(line_heights)
                + INFO_LINE_GAP * (len(wrapped_lines) - 1)
            )
            selected_font = font
            if info_height <= max_info_height:
                break

        if selected_font is None:
            raise RuntimeError("Unable to prepare text layout")
        return selected_font, wrapped_lines, line_heights, info_height
    finally:
        probe.close()


def get_resample_filter():
    from PIL import Image  # type: ignore

    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS


def prepare_image_for_canvas(source_path: Path, scale: float):
    from PIL import Image  # type: ignore

    image = Image.open(source_path)
    target_size = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    if scale < 1.0:
        image.draft("RGB", target_size)
    prepared = image.convert("RGB")
    image.close()
    if prepared.size != target_size:
        prepared = prepared.resize(target_size, get_resample_filter())
    return prepared


def stitch_images(image_paths: list[Path], output_path: Path, metadata: ProductMetadata) -> None:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        stitch_images_with_powershell(image_paths, output_path, metadata)
        return

    dimensions: list[tuple[int, int]] = []
    for path in image_paths:
        with Image.open(path) as image:
            dimensions.append((image.width, image.height))

    total_width = sum(width for width, _ in dimensions)
    image_height = max(height for _, height in dimensions)
    scale = choose_render_scale(total_width, image_height, build_metadata_lines(metadata))
    scaled_widths = [max(1, int(round(width * scale))) for width, _ in dimensions]
    scaled_heights = [max(1, int(round(height * scale))) for _, height in dimensions]
    canvas_width = sum(scaled_widths)
    canvas_image_height = max(scaled_heights)
    selected_font, wrapped_lines, line_heights, info_height = build_text_layout_for_canvas(
        canvas_width,
        canvas_image_height,
        metadata,
    )

    canvas = Image.new(
        "RGB",
        (canvas_width, canvas_image_height + info_height),
        color="white",
    )
    try:
        draw = ImageDraw.Draw(canvas)
        x_offset = 0
        for path in image_paths:
            prepared = prepare_image_for_canvas(path, scale)
            try:
                canvas.paste(prepared, (x_offset, 0))
                x_offset += prepared.width
            finally:
                prepared.close()

        current_y = canvas_image_height + INFO_PADDING_Y
        for index, line in enumerate(wrapped_lines):
            draw.text((INFO_PADDING_X, current_y), line, fill="black", font=selected_font)
            current_y += line_heights[index] + INFO_LINE_GAP

        save_stitched_image(canvas, output_path)
    finally:
        canvas.close()


def stitch_images_with_powershell(
    image_paths: list[Path], output_path: Path, metadata: ProductMetadata
) -> None:
    quoted_paths = ", ".join(f"'{str(path)}'" for path in image_paths)
    quoted_lines = ", ".join(
        quote_powershell_string(line) for line in build_metadata_lines(metadata)
    )
    script = f"""
Add-Type -AssemblyName System.Drawing
$paths = @({quoted_paths})
$lines = @({quoted_lines})
$images = New-Object System.Collections.Generic.List[System.Drawing.Image]
try {{
    foreach ($path in $paths) {{
        $images.Add([System.Drawing.Image]::FromFile($path))
    }}

    $width = 0
    $height = 0
    foreach ($img in $images) {{
        $width += $img.Width
        if ($img.Height -gt $height) {{ $height = $img.Height }}
    }}

    $font = New-Object System.Drawing.Font('Arial', 280)
    $paddingX = 120
    $paddingY = 100
    $lineGap = 60
    $probe = New-Object System.Drawing.Bitmap(10, 10)
    $probeGraphics = [System.Drawing.Graphics]::FromImage($probe)
    try {{
        $lineHeights = New-Object System.Collections.Generic.List[Single]
        $infoHeight = $paddingY * 2
        foreach ($line in $lines) {{
            $size = $probeGraphics.MeasureString($line, $font)
            $lineHeight = [Math]::Ceiling($size.Height)
            $lineHeights.Add($lineHeight)
            $infoHeight += $lineHeight
        }}
        $infoHeight += $lineGap * ($lines.Count - 1)
    }} finally {{
        $probeGraphics.Dispose()
        $probe.Dispose()
    }}

    $canvas = New-Object System.Drawing.Bitmap($width, ($height + $infoHeight))
    try {{
        $graphics = [System.Drawing.Graphics]::FromImage($canvas)
        try {{
            $graphics.Clear([System.Drawing.Color]::White)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
            $offsetX = 0
            foreach ($img in $images) {{
                $graphics.DrawImage($img, $offsetX, 0, $img.Width, $img.Height)
                $offsetX += $img.Width
            }}
            $brush = [System.Drawing.Brushes]::Black
            $textY = $height + $paddingY
            for ($i = 0; $i -lt $lines.Count; $i++) {{
                $graphics.DrawString($lines[$i], $font, $brush, $paddingX, $textY)
                $textY += ($lineHeights[$i] + $lineGap)
            }}
            $format = if ('{output_path.suffix.lower()}' -eq '.png') {{
                [System.Drawing.Imaging.ImageFormat]::Png
            }} else {{
                [System.Drawing.Imaging.ImageFormat]::Jpeg
            }}
            $canvas.Save('{str(output_path)}', $format)
        }} finally {{
            $graphics.Dispose()
        }}
    }} finally {{
        $canvas.Dispose()
        $font.Dispose()
    }}
}} finally {{
    foreach ($img in $images) {{
        $img.Dispose()
    }}
}}
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "PowerShell stitch failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def process_product(
    product_url: str,
    local_category_dir: Path,
    shared_category_dir: Path,
    temp_root: Path,
    context: RunContext,
) -> bool:
    product_cache_path = url_cache_path(context.cache_root, "html", product_url, ".html")
    html = context.request_manager.fetch_text(
        product_url,
        cache_path=product_cache_path,
        ttl_seconds=HTML_CACHE_TTL_SECONDS,
    )
    metadata = extract_product_metadata(html, product_url)
    product_code = sanitize_filename(metadata.product_code)
    image_urls = extract_large_image_urls(html)
    local_output_path = local_category_dir / f"{product_code}.png"
    shared_output_path = shared_category_dir / f"{product_code}.png"

    if shared_output_path.exists():
        if local_output_path.exists():
            try:
                transfer_local_file_to_shared(local_output_path, shared_output_path)
            except OSError as exc:
                print(f"  warning failed to cleanup local file {local_output_path.name}: {exc}")
        print(f"  skip {product_code} (already exists in shared)")
        return False

    if local_output_path.exists():
        try:
            if transfer_local_file_to_shared(local_output_path, shared_output_path):
                print(f"  synced existing local file {local_output_path.name} to shared")
            else:
                print(f"  keep existing local file {local_output_path.name} (shared check failed)")
        except OSError as exc:
            print(f"  warning failed to sync existing local file {local_output_path.name}: {exc}")
        return False

    temp_dir = temp_root / product_code
    temp_dir.mkdir(parents=True, exist_ok=True)
    progress_path = temp_dir / "progress.json"
    progress = load_progress(progress_path)
    downloaded_by_url = progress.setdefault("downloaded_images", {})

    downloaded_paths: list[Path] = []
    try:
        for image_url in image_urls:
            cached_image_path = download_image(
                image_url,
                temp_dir,
                context.request_manager,
            )
            downloaded_paths.append(cached_image_path)
            downloaded_by_url[image_url] = str(cached_image_path)
            save_progress(progress_path, progress)

        local_category_dir.mkdir(parents=True, exist_ok=True)
        shared_category_dir.mkdir(parents=True, exist_ok=True)
        stitch_images(downloaded_paths, local_output_path, metadata)
        print(f"  saved local {local_output_path.name} with {len(downloaded_paths)} images")
        try:
            if transfer_local_file_to_shared(local_output_path, shared_output_path):
                print(f"  transferred to shared and removed local {local_output_path.name}")
            else:
                print(f"  warning shared verification failed, kept local {local_output_path.name}")
        except OSError as exc:
            print(f"  warning failed to transfer {local_output_path.name} to shared: {exc}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return True
    except Exception:
        save_progress(progress_path, progress)
        raise


def collect_category(
    category_name: str,
    category_url: str,
    local_output_root: Path,
    shared_output_root: Path,
    temp_root: Path,
    context: RunContext,
) -> None:
    local_category_dir = local_output_root / category_name
    shared_category_dir = shared_output_root / category_name
    local_category_dir.mkdir(parents=True, exist_ok=True)
    shared_category_dir.mkdir(parents=True, exist_ok=True)
    progress_path = context.progress_root / f"{category_name}.json"
    progress = load_progress(progress_path)
    completed_urls = set(progress.setdefault("completed", []))

    current_url = category_url
    current_page = 1
    product_urls: list[str] = []
    seen_urls: set[str] = set()

    while current_url:
        print(f"[{category_name}] page {current_page}: {current_url}")
        listing_cache_path = url_cache_path(context.cache_root, "html", current_url, ".html")
        html = context.request_manager.fetch_text(
            current_url,
            cache_path=listing_cache_path,
            ttl_seconds=HTML_CACHE_TTL_SECONDS,
        )
        page_product_urls = extract_product_urls(html, current_url)
        new_count = 0
        for product_url in page_product_urls:
            normalized = normalize_product_url(product_url)
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            product_urls.append(product_url)
            new_count += 1
        print(f"[{category_name}] found {new_count} new products")

        next_url = find_next_page_url(html, current_url, current_page)
        if not next_url:
            break
        current_url = next_url
        current_page += 1

    saved_count = 0
    for index, product_url in enumerate(product_urls, start=1):
        normalized = normalize_product_url(product_url)
        if normalized in completed_urls:
            print(f"[{category_name}] product {index}/{len(product_urls)} skip completed")
            continue

        print(f"[{category_name}] product {index}/{len(product_urls)}")
        try:
            if process_product(
                product_url,
                local_category_dir,
                shared_category_dir,
                temp_root / category_name,
                context,
            ):
                saved_count += 1
            completed_urls.add(normalized)
            progress["completed"] = sorted(completed_urls)
            save_progress(progress_path, progress)
        except urllib.error.HTTPError as exc:
            print(f"  skip {product_url} -> HTTP {exc.code}")
        except urllib.error.URLError as exc:
            print(f"  skip {product_url} -> {exc.reason}")
        except Exception as exc:
            print(f"  skip {product_url} -> {exc}")

    print(f"[{category_name}] total products: {len(product_urls)}")
    print(f"[{category_name}] stitched images saved: {saved_count}")


def main() -> int:
    local_output_root = LOCAL_OUTPUT_ROOT
    shared_output_root = SHARED_OUTPUT_ROOT
    temp_root = Path.cwd() / "M&S_temp"
    cache_root = Path.cwd() / ".ms_cache"
    progress_root = temp_root / "progress"
    local_output_root.mkdir(parents=True, exist_ok=True)
    shared_output_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    progress_root.mkdir(parents=True, exist_ok=True)
    moved_count = sync_local_outputs_to_shared(local_output_root, shared_output_root)
    if moved_count:
        print(f"Synced {moved_count} local stitched files to shared storage")
    shutil.rmtree(cache_root / "images", ignore_errors=True)
    context = RunContext(
        request_manager=RequestManager(),
        cache_root=cache_root,
        progress_root=progress_root,
    )

    for category_name, category_url in CATEGORIES.items():
        try:
            collect_category(
                category_name,
                category_url,
                local_output_root,
                shared_output_root,
                temp_root,
                context,
            )
        except Exception as exc:  # pragma: no cover
            print(f"[{category_name}] failed: {exc}", file=sys.stderr)

    print(f"Saved stitched files under shared path: {shared_output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
