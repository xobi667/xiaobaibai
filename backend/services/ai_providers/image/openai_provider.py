"""
OpenAI SDK implementation for image generation.
"""
import logging
import base64
import re
import time
import requests
from io import BytesIO
from typing import Optional, List
from openai import OpenAI
from PIL import Image
from .base import ImageProvider
from config import get_config
from utils.url_utils import normalize_openai_api_base

logger = logging.getLogger(__name__)


class OpenAIImageProvider(ImageProvider):
    """Image generation using OpenAI SDK (compatible with Gemini via proxy)"""
    
    def __init__(self, api_key: str, api_base: str = None, model: str = "gemini-3-pro-image-preview"):
        """
        Initialize OpenAI image provider
        
        Args:
            api_key: API key
            api_base: API base URL (e.g., https://aihubmix.com/v1)
            model: Model name to use
        """
        self.api_key = api_key
        self.api_base = normalize_openai_api_base(api_base) if api_base else None
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.api_base if self.api_base else None,
            timeout=get_config().OPENAI_TIMEOUT,  # set timeout from config
            max_retries=get_config().OPENAI_MAX_RETRIES  # set max retries from config
        )
        self.model = model
    
    def _encode_image_to_base64(self, image: Image.Image) -> str:
        """
        Encode PIL Image to base64 string
        
        Args:
            image: PIL Image object
            
        Returns:
            Base64 encoded string
        """
        buffered = BytesIO()
        # Convert to RGB if necessary (e.g., RGBA images)
        if image.mode in ('RGBA', 'LA', 'P'):
            image = image.convert('RGB')
        image.save(buffered, format="JPEG", quality=95)
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    @staticmethod
    def _is_seedream_model(model: str) -> bool:
        return "seedream" in (model or "").lower()

    @staticmethod
    def _prefers_images_api(model: str) -> bool:
        """
        Best-effort routing between OpenAI-compatible image generation interfaces.

        - Some providers expose image generation via `/v1/chat/completions` (multimodal response).
        - Others require `/v1/images/generations` (Images API).

        Keep this conservative and only route well-known Images-API model families here.
        """
        m = (model or "").strip().lower()
        if not m:
            return False

        # ByteDance Seedream (Doubao) via Images API (as requested).
        if "seedream" in m:
            return True

        # Common Images-API model families in OpenAI-compatible proxies.
        if "gpt-image" in m:
            return True
        if "dall" in m or m.startswith("dalle-"):
            return True

        # Some proxies expose Doubao models via Images API.
        if m.startswith("doubao-"):
            return True

        return False

    @staticmethod
    def _extract_images_api_error_message(data: object) -> str:
        if not isinstance(data, dict):
            return ""

        err = data.get("error")
        if isinstance(err, dict):
            message = err.get("message_zh") or err.get("message") or err.get("msg") or err.get("detail")
            if message:
                return str(message)
            return str(err)
        if err:
            return str(err)

        message = data.get("message") or data.get("msg") or data.get("detail")
        return str(message) if message else ""

    @staticmethod
    def _looks_like_no_channels_error(message: str) -> bool:
        msg = (message or "").lower()
        return ("no available channels" in msg) or ("no available channel" in msg)

    @staticmethod
    def _looks_like_prompt_rejected(message: str) -> bool:
        msg = (message or "").lower()
        return (
            "non-pictorial vocabulary" in msg
            or "content has been flagged" in msg
            or ("flagged" in msg and "content" in msg)
            or ("policy" in msg and "content" in msg)
        )

    @staticmethod
    def _sanitize_images_api_prompt(prompt: str, max_chars: int = 2000) -> str:
        """
        Make a long, instruction-heavy prompt more compatible with Images APIs.

        Some providers (e.g. Yunwu's Seedream) will reject prompts containing XML tags,
        markdown/formatting instructions, or other non-visual vocabulary.
        """
        if not prompt:
            return ""

        text = str(prompt)
        # Remove XML-like tags but keep their contents.
        text = re.sub(r"</?[^>]+>", "\n", text)

        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            lower = line.lower()
            if line.startswith("你是") or lower.startswith("you are "):
                continue
            # Remove explicit format/meta instructions that often trigger filters.
            if "markdown" in lower:
                continue
            if "reference_information" in lower or "design_guidelines" in lower or "reference_images_rules" in lower:
                continue
            if any(token in line for token in ("禁止", "不要", "必须", "不得", "请勿", "务必", "严禁", "严格")):
                continue

            # Drop leading bullets.
            line = line.lstrip("-•* ").strip()
            if not line:
                continue
            lines.append(line)

        cleaned = "\n".join(lines)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if max_chars and len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rstrip()
        return cleaned

    @staticmethod
    def _build_seedream_fallback_prompt(prompt: str, aspect_ratio: str) -> str:
        """
        Seedream is sensitive to prompts with lots of "rules". When rejected as
        non-pictorial, fall back to a short, pictorial poster-style prompt.
        """
        if not prompt:
            return ""

        page_desc = ""
        try:
            m = re.search(r"<page_description>\s*(.*?)\s*</page_description>", str(prompt), re.IGNORECASE | re.DOTALL)
            if m:
                page_desc = (m.group(1) or "").strip()
        except Exception:
            page_desc = ""

        page_desc = re.sub(r"</?[^>]+>", "\n", page_desc)
        page_desc = page_desc.strip()
        page_desc = OpenAIImageProvider._sanitize_images_api_prompt(page_desc, max_chars=800)

        base = f"生成一张电商商品海报，画面比例 {aspect_ratio}，真实商业摄影风格，干净背景，光影自然，高分辨率。"
        if not page_desc:
            return base
        return (
            base
            + "\n海报文案：\n"
            + page_desc
        )

    @staticmethod
    def _guess_images_api_size(aspect_ratio: str) -> str:
        """
        Best-effort mapping to OpenAI Images API `size` values.

        Many OpenAI-compatible proxies only support a limited set of sizes.
        We pick orientation-correct defaults and rely on downstream resizing.
        """
        raw = (aspect_ratio or "").strip()
        try:
            w, h = raw.split(":", 1)
            w_num = float(w)
            h_num = float(h)
            if w_num <= 0 or h_num <= 0:
                raise ValueError("invalid ratio")
            if abs(w_num - h_num) < 1e-6:
                return "1024x1024"
            return "1792x1024" if w_num > h_num else "1024x1792"
        except Exception:
            return "1024x1024"

    def _generate_image_via_images_api(self, prompt: str, aspect_ratio: str, resolution: str) -> Image.Image:
        """
        Generate image via `/v1/images/generations` (DALL·E 3 style).

        Note: most providers ignore `resolution` here; downstream normalization will enforce it.
        """
        base_url = self.api_base or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/images/generations"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Yunwu's Images API is often stricter than OpenAI's and may reject
        # non-standard sizes or `response_format=b64_json` with 5xx.
        is_yunwu = "yunwu.ai" in (base_url or "").lower()
        size = "1024x1024" if is_yunwu else self._guess_images_api_size(aspect_ratio)
        prompt_variants: List[str] = []
        if is_yunwu and self._is_seedream_model(self.model):
            seedream_fallback = self._build_seedream_fallback_prompt(prompt, aspect_ratio)
            if seedream_fallback:
                prompt_variants.append(seedream_fallback)
        if is_yunwu:
            sanitized = self._sanitize_images_api_prompt(prompt)
            if sanitized and sanitized not in prompt_variants:
                prompt_variants.append(sanitized)
        if not prompt_variants:
            prompt_variants = [prompt]

        payload = {
            "model": self.model,
            "prompt": "",
            "n": 1,
            "size": size,
        }

        timeout = get_config().OPENAI_TIMEOUT
        configured_retries = int(get_config().OPENAI_MAX_RETRIES or 0)
        # Images endpoint is more likely to be rate-limited; keep a higher minimum.
        max_attempts = max(configured_retries + 1, 5)

        response: Optional[requests.Response] = None
        last_error_message = ""
        last_prompt_rejected = False

        for prompt_variant in prompt_variants:
            payload["prompt"] = str(prompt_variant or "").strip()
            if not payload["prompt"]:
                continue

            response = None
            last_error_message = ""
            last_prompt_rejected = False

            for attempt in range(1, max_attempts + 1):
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                except requests.RequestException as e:
                    if attempt >= max_attempts:
                        raise
                    wait = min(2.0 * (2 ** (attempt - 1)), 20.0)
                    logger.warning(
                        "Images API request error, retrying in %.1fs (attempt %s/%s): %s",
                        wait,
                        attempt,
                        max_attempts,
                        e,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code in (429, 500, 502, 503, 504):
                    try:
                        data = response.json()
                        last_error_message = self._extract_images_api_error_message(data)
                    except Exception:
                        last_error_message = ""

                    # Prompt/content rejections are not transient; don't retry.
                    if response.status_code == 500 and self._looks_like_prompt_rejected(last_error_message):
                        last_prompt_rejected = True
                        logger.warning(
                            "Images API rejected prompt as non-pictorial (model=%s): %s",
                            self.model,
                            last_error_message,
                        )
                        break

                    if attempt < max_attempts:
                        retry_after = response.headers.get("Retry-After")
                        wait = 0.0
                        if retry_after:
                            try:
                                wait = float(str(retry_after).strip())
                            except Exception:
                                wait = 0.0
                        if wait <= 0:
                            wait = min(2.0 * (2 ** (attempt - 1)), 30.0)

                        logger.warning(
                            "Images API throttled (HTTP %s), retrying in %.1fs (attempt %s/%s)%s",
                            response.status_code,
                            wait,
                            attempt,
                            max_attempts,
                            f": {last_error_message}" if last_error_message else "",
                        )
                        time.sleep(wait)
                        continue
                break

            if response is None:
                continue
            if response.status_code < 400:
                break
            if last_prompt_rejected:
                # Try the next prompt variant (if any) instead of surfacing the same rejection.
                continue
            break

        if response is None:
            raise ValueError("Images API 请求失败：没有收到响应")

        try:
            data = response.json()
        except Exception:
            response.raise_for_status()
            raise ValueError("Images API returned non-JSON response")

        if response.status_code >= 400:
            if response.status_code == 429:
                raise ValueError(
                    "生图接口触发限流（429 Too Many Requests）。请稍等 10-30 秒后重试；如在批量生图，建议降低并发或逐张生成。"
                )

            message = self._extract_images_api_error_message(data) or last_error_message
            hint = ""
            if response.status_code == 401:
                hint = "（API Key 无效或未填写）"
            elif response.status_code == 403:
                hint = "（无权限/分组无该模型权限）"
            elif response.status_code == 404:
                hint = "（该服务可能不支持 /v1/images/generations）"
            elif response.status_code == 503 and self._looks_like_no_channels_error(message):
                hint = "（该 Key/分组没有该模型的可用通道，请在云雾控制台确认权限或换模型）"
            elif self._looks_like_prompt_rejected(message):
                hint = "（提示词被判定为“非图像描述词汇/触发风控”，请简化为画面描述再试）"

            raise ValueError(
                f"生图失败（HTTP {response.status_code}，model={self.model}）{hint}"
                + (f"：{message}" if message else "")
            )

        if not isinstance(data, dict):
            raise ValueError("Images API returned unexpected JSON shape")

        items = data.get("data") or []
        if not items or not isinstance(items, list):
            raise ValueError("Images API returned empty data")

        first = items[0] if isinstance(items[0], dict) else None
        if not first:
            raise ValueError("Images API returned invalid data item")

        b64_json = first.get("b64_json") or first.get("b64") or first.get("base64")
        if b64_json:
            image_data = base64.b64decode(b64_json)
            image = Image.open(BytesIO(image_data))
            image.load()
            return image

        image_url = first.get("url")
        if image_url:
            img_resp = requests.get(str(image_url), timeout=30, stream=True)
            img_resp.raise_for_status()
            image = Image.open(BytesIO(img_resp.content))
            image.load()
            return image

        raise ValueError("Images API did not return b64_json or url")

    def _generate_image_via_chat_completions(
        self,
        prompt: str,
        ref_images: Optional[List[Image.Image]] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "2K",
    ) -> Optional[Image.Image]:
        # Build message content
        content = []

        # Add reference images first (if any)
        if ref_images:
            for ref_img in ref_images:
                base64_image = self._encode_image_to_base64(ref_img)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    }
                )

        # Add text prompt
        content.append({"type": "text", "text": prompt})

        logger.debug(
            f"Calling OpenAI API for image generation with {len(ref_images) if ref_images else 0} reference images..."
        )
        logger.debug(
            f"Config - aspect_ratio: {aspect_ratio}, resolution: {resolution} (may be ignored by some OpenAI-compatible proxies)"
        )

        # Note: resolution is not supported in OpenAI format, only aspect_ratio via system message
        # Modalites is removed for compatibility with proxy providers like Yunwu
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": f"aspect_ratio={aspect_ratio};resolution={resolution}"},
                {"role": "user", "content": content},
            ],
            max_tokens=4096,  # Give enough tokens for multimodal response
        )

        logger.debug("OpenAI API call completed")

        # Extract image from response - handle different response formats
        message = response.choices[0].message

        # Debug: log available attributes
        logger.debug(f"Response message attributes: {dir(message)}")

        # Try multi_mod_content first (custom format from some proxies)
        if hasattr(message, "multi_mod_content") and message.multi_mod_content:
            parts = message.multi_mod_content
            for part in parts:
                if "text" in part:
                    logger.debug(
                        f"Response text: {part['text'][:100] if len(part['text']) > 100 else part['text']}"
                    )
                if "inline_data" in part:
                    image_data = base64.b64decode(part["inline_data"]["data"])
                    image = Image.open(BytesIO(image_data))
                    logger.debug(f"Successfully extracted image: {image.size}, {image.mode}")
                    return image

        # Try standard OpenAI content format (list of content parts)
        if hasattr(message, "content") and message.content:
            # If content is a list (multimodal response)
            if isinstance(message.content, list):
                for part in message.content:
                    if isinstance(part, dict):
                        # Handle image_url type
                        if part.get("type") == "image_url":
                            image_url = part.get("image_url", {}).get("url", "")
                            if image_url.startswith("data:image"):
                                # Extract base64 data from data URL
                                base64_data = image_url.split(",", 1)[1]
                                image_data = base64.b64decode(base64_data)
                                image = Image.open(BytesIO(image_data))
                                logger.debug(
                                    f"Successfully extracted image from content: {image.size}, {image.mode}"
                                )
                                return image
                        # Handle text type
                        elif part.get("type") == "text":
                            text = part.get("text", "")
                            if text:
                                logger.debug(f"Response text: {text[:100] if len(text) > 100 else text}")
                    elif hasattr(part, "type"):
                        # Handle as object with attributes
                        if part.type == "image_url":
                            image_url = getattr(part, "image_url", {})
                            if isinstance(image_url, dict):
                                url = image_url.get("url", "")
                            else:
                                url = getattr(image_url, "url", "")
                            if url.startswith("data:image"):
                                base64_data = url.split(",", 1)[1]
                                image_data = base64.b64decode(base64_data)
                                image = Image.open(BytesIO(image_data))
                                logger.debug(
                                    f"Successfully extracted image from content object: {image.size}, {image.mode}"
                                )
                                return image
            # If content is a string, try to extract image from it
            elif isinstance(message.content, str):
                content_str = message.content
                logger.debug(
                    f"Response content (string): {content_str[:200] if len(content_str) > 200 else content_str}"
                )

                # Try to extract Markdown image URL: ![...](url)
                markdown_pattern = r"!\[.*?\]\((https?://[^\s\)]+)\)"
                markdown_matches = re.findall(markdown_pattern, content_str)
                if markdown_matches:
                    image_url = markdown_matches[0]  # Use the first image URL found
                    logger.debug(f"Found Markdown image URL: {image_url}")
                    try:
                        response = requests.get(image_url, timeout=30, stream=True)
                        response.raise_for_status()
                        image = Image.open(BytesIO(response.content))
                        image.load()  # Ensure image is fully loaded
                        logger.debug(
                            f"Successfully downloaded image from Markdown URL: {image.size}, {image.mode}"
                        )
                        return image
                    except Exception as download_error:
                        logger.warning(f"Failed to download image from Markdown URL: {download_error}")

                # Try to extract plain URL (not in Markdown format)
                url_pattern = r"(https?://[^\s\)\]]+\.(?:png|jpg|jpeg|gif|webp|bmp)(?:\?[^\s\)\]]*)?)"
                url_matches = re.findall(url_pattern, content_str, re.IGNORECASE)
                if url_matches:
                    image_url = url_matches[0]
                    logger.debug(f"Found plain image URL: {image_url}")
                    try:
                        response = requests.get(image_url, timeout=30, stream=True)
                        response.raise_for_status()
                        image = Image.open(BytesIO(response.content))
                        image.load()
                        logger.debug(
                            f"Successfully downloaded image from plain URL: {image.size}, {image.mode}"
                        )
                        return image
                    except Exception as download_error:
                        logger.warning(f"Failed to download image from plain URL: {download_error}")

                # Try to extract base64 data URL from string
                base64_pattern = r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)"
                base64_matches = re.findall(base64_pattern, content_str)
                if base64_matches:
                    base64_data = base64_matches[0]
                    logger.debug("Found base64 image data in string")
                    try:
                        image_data = base64.b64decode(base64_data)
                        image = Image.open(BytesIO(image_data))
                        logger.debug(f"Successfully extracted base64 image from string: {image.size}, {image.mode}")
                        return image
                    except Exception as decode_error:
                        logger.warning(f"Failed to decode base64 image from string: {decode_error}")

        # Log raw response for debugging
        logger.warning(f"Unable to extract image. Raw message type: {type(message)}")
        logger.warning(f"Message content type: {type(getattr(message, 'content', None))}")
        logger.warning(f"Message content: {getattr(message, 'content', 'N/A')}")

        raise ValueError("No valid multimodal response received from OpenAI API")
     
    def generate_image(
        self,
        prompt: str,
        ref_images: Optional[List[Image.Image]] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "2K"
    ) -> Optional[Image.Image]:
        """
        Generate image using OpenAI SDK
        
        Note: OpenAI format does NOT support 4K images, defaults to 1K
        
        Args:
            prompt: The image generation prompt
            ref_images: Optional list of reference images
            aspect_ratio: Image aspect ratio
            resolution: Image resolution (only 1K supported, parameter ignored)
            
        Returns:
            Generated PIL Image object, or None if failed
        """
        try:
            prefer_images_api = self._prefers_images_api(self.model)

            if prefer_images_api:
                if ref_images:
                    logger.warning(
                        "Model=%s uses /v1/images/generations; reference images will be ignored (count=%s).",
                        self.model,
                        len(ref_images),
                    )
                try:
                    return self._generate_image_via_images_api(prompt, aspect_ratio, resolution)
                except Exception as e:
                    logger.warning(
                        "Images API failed for model=%s, falling back to chat.completions: %s",
                        self.model,
                        e,
                    )

            try:
                return self._generate_image_via_chat_completions(prompt, ref_images, aspect_ratio, resolution)
            except Exception as e:
                # If the proxy indicates this model has no chat channel, try Images API as fallback.
                if not prefer_images_api and self._looks_like_no_channels_error(str(e)):
                    if ref_images:
                        logger.warning(
                            "Images API fallback for model=%s will ignore reference images (count=%s).",
                            self.model,
                            len(ref_images),
                        )
                    return self._generate_image_via_images_api(prompt, aspect_ratio, resolution)
                raise

        except Exception as e:
            raw_message = str(e)
            friendly_hint = ""
            if self._looks_like_no_channels_error(raw_message):
                friendly_hint = "（云雾提示：该模型没有可用通道/权限，请在云雾控制台检查分组权限或更换模型）"

            error_detail = (
                f"Error generating image with OpenAI (model={self.model}): {type(e).__name__}: {raw_message}"
                f"{friendly_hint}"
            )
            logger.error(error_detail, exc_info=True)
            raise Exception(error_detail) from e
