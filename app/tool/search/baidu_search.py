from typing import List
from urllib.parse import urljoin

from baidusearch.baidusearch import search

from app.tool.search.base import SearchItem, WebSearchEngine

# 百度基础 URL，用于将相对路径转换为绝对路径
BAIDU_BASE_URL = "https://www.baidu.com"


class BaiduSearchEngine(WebSearchEngine):
    def perform_search(
        self, query: str, num_results: int = 10, *args, **kwargs
    ) -> List[SearchItem]:
        """
        百度搜索引擎。

        返回根据 SearchItem 模型格式化的结果。
        """
        raw_results = search(query, num_results=num_results)

        # 将原始结果转换为 SearchItem 格式
        results = []
        for i, item in enumerate(raw_results):
            if isinstance(item, str):
                # 如果只是 URL，确保是绝对路径
                url = self._ensure_absolute_url(item)
                results.append(
                    SearchItem(title=f"百度结果 {i+1}", url=url, description=None)
                )
            elif isinstance(item, dict):
                # 如果是包含详细信息的字典，确保 URL 是绝对路径
                raw_url = item.get("url", "")
                url = self._ensure_absolute_url(raw_url)
                results.append(
                    SearchItem(
                        title=item.get("title", f"百度结果 {i+1}"),
                        url=url,
                        description=item.get("abstract", None),
                    )
                )
            else:
                # 直接尝试获取属性
                try:
                    raw_url = getattr(item, "url", "")
                    url = self._ensure_absolute_url(raw_url)
                    results.append(
                        SearchItem(
                            title=getattr(item, "title", f"百度结果 {i+1}"),
                            url=url,
                            description=getattr(item, "abstract", None),
                        )
                    )
                except Exception:
                    # 回退到基本结果
                    results.append(
                        SearchItem(
                            title=f"百度结果 {i+1}", url=str(item), description=None
                        )
                    )

        return results

    def _ensure_absolute_url(self, url: str) -> str:
        """
        确保 URL 是绝对路径。
        如果 URL 是相对路径（以 / 开头但不以 // 开头），则转换为绝对路径。
        """
        if not url:
            return url
        # 如果已经是完整的 URL（以 http:// 或 https:// 开头），直接返回
        if url.startswith(("http://", "https://")):
            return url
        # 如果是相对路径，使用 urljoin 转换为绝对路径
        return urljoin(BAIDU_BASE_URL, url)
