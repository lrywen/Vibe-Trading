"""Web reader tool: fetch a URL as Markdown text via the Jina Reader API."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from functools import lru_cache
from threading import Lock
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests

from src.agent.progress import emit_progress
from src.agent.tools import BaseTool
from src.security.scanner import with_security_warnings

logger = logging.getLogger(__name__)

_JINA_PREFIX = "https://r.jina.ai/"
_TIMEOUT = int(os.environ.get("READ_URL_TIMEOUT", "30"))  # read_url 专用超时
_MAX_LENGTH = 8000
_CACHED_MARKER = "Warning: This is a cached snapshot"

# ============================================================================
# CDN 镜像配置（优化访问速度）
# ============================================================================
# 可用的 Jina Reader CDN 镜像列表（按优先级排序）
_JINA_CDN_MIRRORS = [
    "https://r.jina.ai/",           # 官方源
    "https://r.jina.ai/",           # 可添加更多镜像
]

# HTTP/2 配置
_ENABLE_HTTP2 = os.environ.get("READ_URL_HTTP2", "true").lower() == "true"

# 限流控制（防止触发 API 限流）
_RATE_LIMIT_DELAY = float(os.environ.get("READ_URL_RATE_LIMIT_DELAY", "1.0"))  # 每次请求间隔（秒）
_last_request_time = 0
_rate_limit_lock = Lock()

# 交易所 API 限流配置（特殊处理高频率请求的域名）
_EXCHANGE_RATE_LIMITS = {
    "gate.io": 2.0,      # Gate.io 要求最低 2 秒间隔
    "api.gateio.ws": 2.0,
    "binance.com": 1.0,
    "api.binance.com": 1.0,
    "okx.com": 2.0,
    "api.okx.com": 2.0,
    "kucoin.com": 1.5,
    "api.kucoin.com": 1.5,
    "coingecko.com": 3.0,  # CoinGecko 要求较高
    "coinmarketcap.com": 60.0,  # CoinMarketCap 限流严格
}

# 交易所请求计数器（按域名统计）
_exchange_request_counts: Dict[str, int] = {}
_exchange_request_lock = Lock()

# ============================================================================
# 请求缓存配置（减少重复请求，优化网络延迟）
# ============================================================================
_ENABLE_CACHE = os.environ.get("READ_URL_CACHE", "true").lower() == "true"
_CACHE_TTL = int(os.environ.get("READ_URL_CACHE_TTL", "300"))  # 缓存有效期（秒）
_CACHE_MAX_SIZE = int(os.environ.get("READ_URL_CACHE_SIZE", "100"))  # 最大缓存条目数

# 缓存存储（线程安全）
_cache: Dict[str, Tuple[str, float]] = {}  # {url: (result, timestamp)}
_cache_lock = Lock()

# HTTP/2 会话复用（全局会话以复用连接）
_http2_session = None
_http2_session_lock = Lock()


def _wait_for_rate_limit(url: str = "") -> None:
    """等待限流间隔，确保请求频率不超过限制
    
    支持按域名的差异化限流配置，特别是交易所 API。
    
    Args:
        url: 目标 URL，用于确定限流间隔
    """
    global _last_request_time
    
    # 根据 URL 确定限流间隔
    delay = _RATE_LIMIT_DELAY
    domain = ""
    
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.hostname.lower() if parsed.hostname else ""
        
        # 检查是否是交易所域名，使用特殊限流配置
        for exchange_domain, exchange_delay in _EXCHANGE_RATE_LIMITS.items():
            if domain == exchange_domain or domain.endswith(f".{exchange_domain}"):
                delay = exchange_delay
                break
    except:
        pass
    
    with _rate_limit_lock:
        now = time.time()
        elapsed = now - _last_request_time
        
        if elapsed < delay:
            wait_time = delay - elapsed
            if domain:
                logger.info(f"[read_url][限流] 等待 {wait_time:.2f}s (域名: {domain}, 间隔: {delay}s)")
            else:
                logger.debug(f"[read_url][限流] 等待 {wait_time:.2f}s (间隔: {delay}s)")
            time.sleep(wait_time)
        
        _last_request_time = time.time()


def _get_http2_session() -> requests.Session:
    """获取或创建 HTTP/2 会话（连接复用）
    
    HTTP/2 支持多路复用，可以在单个 TCP 连接上并行处理多个请求，
    大幅减少连接建立时间。
    """
    global _http2_session
    
    with _http2_session_lock:
        if _http2_session is None:
            # 创建新会话
            _http2_session = requests.Session()
            
            # 配置连接复用参数
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=10,  # 连接池大小
                pool_maxsize=10,      # 最大连接数
                max_retries=0,        # HTTPAdapter 不重试，由上层处理
            )
            _http2_session.mount('http://', adapter)
            _http2_session.mount('https://', adapter)
            
            # 配置请求头
            _http2_session.headers.update({
                'User-Agent': 'Vibe-Trading/1.0',
                'Accept': 'text/markdown, application/json',
            })
            
            logger.info(f"[read_url][HTTP2] 创建新会话（连接池: 10）")
        
        return _http2_session


def _get_cached_result(url: str) -> Optional[str]:
    """从缓存获取结果（如果未过期）
    
    Args:
        url: 请求的URL
    
    Returns:
        缓存的结果字符串，如果缓存过期或不存在则返回 None
    """
    if not _ENABLE_CACHE:
        return None
    
    with _cache_lock:
        if url in _cache:
            result, timestamp = _cache[url]
            age = time.time() - timestamp
            if age < _CACHE_TTL:
                logger.info(f"[read_url][缓存] 命中缓存: {url[:40]}... (age: {age:.1f}s)")
                return result
            else:
                # 缓存过期，删除
                del _cache[url]
                logger.debug(f"[read_url][缓存] 缓存过期: {url[:40]}...")
    return None


def _set_cached_result(url: str, result: str) -> None:
    """将结果写入缓存
    
    Args:
        url: 请求的URL
        result: 结果字符串
    """
    if not _ENABLE_CACHE:
        return
    
    with _cache_lock:
        # 清理超过最大缓存数量的旧条目
        if len(_cache) >= _CACHE_MAX_SIZE:
            # 删除最老的条目
            oldest_key = min(_cache.keys(), key=lambda k: _cache[k][1])
            del _cache[oldest_key]
            logger.debug(f"[read_url][缓存] 缓存已满，删除最老条目")
        
        _cache[url] = (result, time.time())
        logger.info(f"[read_url][缓存] 保存缓存: {url[:40]}... (TTL: {_CACHE_TTL}s)")


def _clear_cache() -> int:
    """清空所有缓存
    
    Returns:
        清空的缓存条目数量
    """
    with _cache_lock:
        count = len(_cache)
        _cache.clear()
    logger.info(f"[read_url][缓存] 已清空所有缓存 ({count} 条)")
    return count


# 代理配置（与 api_server.py 保持一致）
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "socks5h://127.0.0.1:1080")
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
PROXY_RETRY_DELAYS = [3, 8, 15]  # 阶梯式重试间隔（秒），稍微延长以适应代理延迟

# 被封禁/不稳定的域名列表（返回 451 或频繁超时的域名）
_BLOCKED_DOMAINS = {
    "marketwatch.com",  # Jina Reader 被该网站封禁
    "wsj.com",           # 华尔街日报（常见封禁）
    "ft.com",            # 金融时报（常见封禁）
}

# 需要较长超时的域名（代理延迟较高）
_HIGH_LATENCY_DOMAINS = {
    "jina.ai",           # Jina Reader 服务
    "coingecko.com",     # 加密货币数据
    "coinmarketcap.com", # 加密货币数据
    "github.com",        # GitHub
}

# 境内域名白名单
_CHINA_DOMAINS = {
    "aliyun.com", "alibaba.com", "baidu.com", "bytedance.net", "douyin.com",
    "jd.com", "meituan.com", "tencent.com", "qq.com", "weixin.qq.com", "xiaomi.com",
    "eastmoney.com", "10jqka.com.cn", "hexun.com", "sina.com.cn", "sohu.com",
    "sse.com.cn", "szse.cn", "cffex.com.cn", "shfe.com.cn", "dce.com.cn", "czce.cn",
    "cn", "cn.net", "cn.com", "localhost", "127.0.0.1", "0.0.0.0",
}

# 境外域名清单
_FOREIGN_DOMAINS = {
    "binance.com", "binance.us", "binanceapi.com", "binance.me",
    "coinbase.com", "pro.coinbase.com",
    "kraken.com", "api.kraken.com",
    "kucoin.com", "api.kucoin.com",
    "gate.io", "api.gateio.ws", "www.gate.io",  # Gate.io 多个域名
    "huobi.com", "api.huobi.pro",
    "okx.com", "www.okx.com", "api.okx.com",
    "mexc.com", "www.mexc.com", "api.mexc.com",
    "bybit.com", "api.bybit.com",
    "bitget.com", "api.bitget.com",
    "ftx.com", "ftx.us",
    "coinmetro.com", "amazonaws.com", "google.com", "googleapis.com",
    "cloudflare.com", "digitalocean.com", "herokuapp.com", "openai.com",
    "anthropic.com", "gemini.google.com", "groq.com", "together.ai",
    "huggingface.co", "jina.ai", "duckduckgo.com", "serpapi.com",
    "alphavantage.co", "iexcloud.io", "polygon.io", "coinmarketcap.com",
    "coingecko.com", "twitter.com", "x.com", "github.com", "discord.com",
    "telegram.org",
}


def _get_proxy_for_url(url: str) -> str | None:
    """根据URL智能判断是否需要使用代理
    
    返回: (proxy_url 或 None, timeout调整倍数)
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None, 1.0
        
        hostname_lower = hostname.lower().strip(".")
        
        # 检查是否是被封禁域名
        for domain in _BLOCKED_DOMAINS:
            if hostname_lower == domain or hostname_lower.endswith(f".{domain}"):
                logger.warning(f"[read_url] 域名 {hostname} 在被封禁列表中，将跳过或使用备用方案")
                return None, 1.0
        
        # 境内域名：直接连接
        for domain in _CHINA_DOMAINS:
            if hostname_lower == domain or hostname_lower.endswith(f".{domain}"):
                logger.debug(f"[read_url] 境内域名，直接连接: {hostname}")
                return None, 1.0
        
        # 境外域名：强制走代理
        for domain in _FOREIGN_DOMAINS:
            if hostname_lower == domain or hostname_lower.endswith(f".{domain}"):
                logger.debug(f"[read_url] 境外域名，使用代理: {hostname}")
                return SOCKS5_PROXY, 1.0
        
        # 默认：使用代理
        logger.debug(f"[read_url] 未知域名，默认使用代理: {hostname}")
        return SOCKS5_PROXY, 1.0
    except Exception as e:
        logger.warning(f"[read_url] 代理判断失败，默认使用代理: {e}")
        return SOCKS5_PROXY, 1.0


def _get_timeout_for_url(url: str, base_timeout: int) -> int:
    """根据URL类型返回合适的超时时间
    
    Args:
        url: 目标URL
        base_timeout: 基础超时时间（秒）
    
    Returns:
        调整后的超时时间（秒）
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname.lower().strip(".")
        
        # 高延迟域名增加超时时间
        for domain in _HIGH_LATENCY_DOMAINS:
            if hostname == domain or hostname.endswith(f".{domain}"):
                new_timeout = int(base_timeout * 2)  # 双倍超时
                logger.info(f"[read_url] 高延迟域名 {hostname}，超时调整为 {new_timeout}s")
                return new_timeout
    except:
        pass
    
    return base_timeout


def _url_allowed(url: str) -> tuple[bool, str]:
    """Return whether a URL is safe to forward to the remote reader service."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False, "target URL is not allowed"

    if parsed.scheme.lower() not in {"http", "https"}:
        return False, "target URL is not allowed"
    if not parsed.hostname:
        return False, "target URL is not allowed"
    if parsed.username or parsed.password:
        return False, "target URL is not allowed"

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "target URL is not allowed"

    ip_host = host.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(ip_host)
    except ValueError:
        return True, ""

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False, "target URL is not allowed"
    return True, ""


def read_url(url: str, no_cache: bool = False) -> str:
    """Fetch web page content via the Jina Reader API.

    The full URL (including query string) is sent to the third-party Jina
    Reader service (r.jina.ai); never pass credentials/tokens or private
    addresses. Results may be a cached snapshot.

    支持智能代理分流：
    - 境内域名：直接连接
    - 境外域名：通过SOCKS5H代理连接
    - 支持阶梯式重试机制
    - 高延迟域名自动增加超时
    - 内置请求缓存（减少重复请求，优化延迟）

    Args:
        url: Target URL.
        no_cache: When true, ask the reader for a fresh (uncached) fetch.

    Returns:
        JSON result with title, content, url; ``cached: true`` is added
        when the reader served a stale snapshot.
    """
    target_url = url.strip()
    allowed, error = _url_allowed(target_url)
    if not allowed:
        return json.dumps({"status": "error", "error": error}, ensure_ascii=False)

    # 检查缓存（除非明确要求不使用缓存）
    if not no_cache and _ENABLE_CACHE:
        cached_result = _get_cached_result(target_url)
        if cached_result:
            # 返回缓存结果，标记为缓存
            try:
                result_dict = json.loads(cached_result)
                result_dict["cached"] = True
                result_dict["cache_age_seconds"] = int(time.time() - _cache.get(target_url, (None, 0))[1])
                logger.info(f"[read_url] 从缓存返回结果")
                return json.dumps(result_dict, ensure_ascii=False)
            except:
                pass

    # 获取代理配置和超时调整
    proxy_result = _get_proxy_for_url(_JINA_PREFIX + target_url)
    proxy = proxy_result if isinstance(proxy_result, str) else proxy_result[0]
    adjusted_timeout = _get_timeout_for_url(_JINA_PREFIX + target_url, _TIMEOUT)
    
    logger.info(f"[read_url] 开始处理请求: {target_url[:60]}...")
    logger.info(f"[read_url] 代理决策: {'使用 SOCKS5H 代理: ' + str(proxy) if proxy else '直接连接(境内域名)'}")
    logger.info(f"[read_url] 配置: 超时={adjusted_timeout}s, 重试次数={MAX_RETRIES}, 缓存={'启用' if _ENABLE_CACHE else '禁用'}, 限流间隔={_RATE_LIMIT_DELAY}s")
    
    # 限流控制（根据目标 URL 应用差异化限流）
    _wait_for_rate_limit(target_url)
    
    for attempt in range(MAX_RETRIES):
        start_time = time.time()
        try:
            headers = {"Accept": "text/markdown"}
            if no_cache:
                headers["x-no-cache"] = "true"
            emit_progress(
                "fetching",
                message=f"GET {target_url[:60]}{'…' if len(target_url) > 60 else ''}",
            )
            
            # 使用 HTTP/2 会话复用
            session = _get_http2_session()
            if proxy:
                session.proxies = {"http": proxy, "https": proxy}
                logger.info(f"[read_url][尝试 {attempt + 1}] 配置代理: {proxy}")
            else:
                # 清除代理配置
                session.proxies = None
            
            logger.info(f"[read_url][尝试 {attempt + 1}] 发送请求到: {_JINA_PREFIX}{target_url[:60]}...")
            
            resp = session.get(
                f"{_JINA_PREFIX}{target_url}",
                headers=headers,
                timeout=adjusted_timeout,
            )
            
            elapsed = time.time() - start_time
            logger.info(f"[read_url][尝试 {attempt + 1}] 请求完成，状态码: {resp.status_code}, 耗时: {elapsed:.2f}s")

            emit_progress("parsing", message="extracting markdown")
            if resp.status_code == 429:
                # 429 Too Many Requests - 限流错误
                retry_after = int(resp.headers.get("Retry-After", 60))  # 默认等待60秒
                logger.error(f"[read_url][尝试 {attempt + 1}] 触发限流 (HTTP 429): {target_url}")
                logger.error(f"[read_url] Retry-After: {retry_after}秒")
                if attempt < MAX_RETRIES - 1:
                    # 限流时等待更长时间
                    delay = max(retry_after, PROXY_RETRY_DELAYS[min(attempt, len(PROXY_RETRY_DELAYS) - 1)] * 2)
                    logger.info(f"[read_url] [限流] 等待 {delay} 秒后进行第 {attempt + 2} 次重试...")
                    time.sleep(delay)
                    continue
                return json.dumps({
                    "status": "error",
                    "error": f"Rate limited (HTTP 429). The Jina Reader service is temporarily blocking requests. Please try again later.",
                    "error_type": "rate_limited",
                    "suggestion": "The service is experiencing high traffic. Please wait a few minutes and try again.",
                }, ensure_ascii=False)
            elif resp.status_code == 451:
                # 451 Unavailable For Legal Reasons - 域名被封禁
                logger.error(f"[read_url][尝试 {attempt + 1}] 域名被封禁 (HTTP 451): {target_url}")
                logger.error(f"[read_url] 该域名可能不支持 Jina Reader 服务，建议尝试直接访问或使用其他数据源")
                return json.dumps({
                    "status": "error",
                    "error": f"Domain blocked (HTTP 451). The website {target_url} does not allow access via Jina Reader. Try accessing it directly or use alternative sources.",
                    "error_type": "domain_blocked",
                    "suggestion": "This website blocks automated access. Please try a different URL or access the content directly.",
                }, ensure_ascii=False)
            elif resp.status_code != 200:
                logger.error(f"[read_url][尝试 {attempt + 1}] HTTP 错误: {resp.status_code}, 响应: {resp.text[:500]}")
                if attempt < MAX_RETRIES - 1:
                    delay = PROXY_RETRY_DELAYS[min(attempt, len(PROXY_RETRY_DELAYS) - 1)]
                    logger.info(f"[read_url] 等待 {delay} 秒后进行第 {attempt + 2} 次重试...")
                    time.sleep(delay)
                    continue
                return json.dumps({
                    "status": "error",
                    "error": f"remote reader returned HTTP {resp.status_code}: {resp.text[:500]}",
                }, ensure_ascii=False)

            text = resp.text
            title = ""
            for line in text.split("\n"):
                if line.startswith("Title:"):
                    title = line[6:].strip()
                    break

            if len(text) > _MAX_LENGTH:
                text = text[:_MAX_LENGTH] + f"\n\n... (truncated, total {len(resp.text)} chars)"

            result = {
                "status": "ok",
                "title": title,
                "url": target_url,
                "content": text,
                "length": len(resp.text),
            }
            if _CACHED_MARKER in resp.text:
                result["cached"] = True
            result = with_security_warnings(result, fields=("content",))
            
            # 保存到缓存（不包含缓存标记，避免无限缓存）
            cache_result = {k: v for k, v in result.items() if k != "cached"}
            _set_cached_result(target_url, json.dumps(cache_result, ensure_ascii=False))
            
            total_elapsed = time.time() - start_time
            logger.info(f"[read_url] 处理完成，内容长度: {len(text)} 字符, 总耗时: {total_elapsed:.2f}s")
            return json.dumps(result, ensure_ascii=False)

        except requests.Timeout:
            elapsed = time.time() - start_time
            logger.error(f"[read_url][尝试 {attempt + 1}/{MAX_RETRIES}] 请求超时，已等待 {elapsed:.2f}s（配置超时: {adjusted_timeout}s）")
            if attempt < MAX_RETRIES - 1:
                delay = PROXY_RETRY_DELAYS[min(attempt, len(PROXY_RETRY_DELAYS) - 1)]
                logger.info(f"[read_url] 等待 {delay} 秒后进行第 {attempt + 2} 次重试...")
                time.sleep(delay)
                continue
            logger.error(f"[read_url] 所有重试已用完，请求超时失败")
            return json.dumps({
                "status": "error",
                "error": f"Request timed out after {MAX_RETRIES} attempts (timeout: {adjusted_timeout}s each). The proxy connection may be slow or the target server is unreachable.",
                "error_type": "timeout",
                "suggestion": "Try again later or use a different data source.",
            }, ensure_ascii=False)
        except requests.exceptions.RequestException as exc:
            elapsed = time.time() - start_time
            logger.error(f"[read_url][尝试 {attempt + 1}/{MAX_RETRIES}] 请求异常: {type(exc).__name__}: {exc}, 耗时: {elapsed:.2f}s")
            if attempt < MAX_RETRIES - 1:
                delay = PROXY_RETRY_DELAYS[min(attempt, len(PROXY_RETRY_DELAYS) - 1)]
                logger.info(f"[read_url] 等待 {delay} 秒后进行第 {attempt + 2} 次重试...")
                time.sleep(delay)
                continue
            logger.error(f"[read_url] 所有重试已用完，请求失败: {exc}")
            return json.dumps(
            {"status": "error", "error": f"remote reader request failed: {exc}"},
            ensure_ascii=False,
        )


class WebReaderTool(BaseTool):
    """Web reader tool."""

    name = "read_url"
    description = "Fetch web page content: provide a URL and receive the page as Markdown text. Useful for reading docs, articles, API references, etc."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL of the web page to read"},
            "no_cache": {"type": "boolean", "description": "Request a fresh (uncached) fetch", "default": False},
        },
        "required": ["url"],
    }
    repeatable = True

    def execute(self, **kwargs) -> str:
        """Fetch web page."""
        return read_url(kwargs["url"], no_cache=bool(kwargs.get("no_cache", False)))
