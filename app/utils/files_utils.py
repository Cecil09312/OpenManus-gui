import os


# 要从操作中排除的文件
EXCLUDED_FILES = {
    ".DS_Store",
    ".gitignore",
    "package-lock.json",
    "postcss.config.js",
    "postcss.config.mjs",
    "jsconfig.json",
    "components.json",
    "tsconfig.tsbuildinfo",
    "tsconfig.json",
}

# 要从操作中排除的目录
EXCLUDED_DIRS = {"node_modules", ".next", "dist", "build", ".git"}

# 要从操作中排除的文件扩展名
EXCLUDED_EXT = {
    ".ico",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".db",
    ".sql",
}


def should_exclude_file(rel_path: str) -> bool:
    """根据路径、名称或扩展名检查文件是否应被排除

    参数：
        rel_path: 要检查的文件相对路径

    返回：
        如果文件应被排除则返回 True，否则返回 False
    """
    # 检查文件名
    filename = os.path.basename(rel_path)
    if filename in EXCLUDED_FILES:
        return True

    # 检查目录
    dir_path = os.path.dirname(rel_path)
    if any(excluded in dir_path for excluded in EXCLUDED_DIRS):
        return True

    # 检查扩展名
    _, ext = os.path.splitext(filename)
    if ext.lower() in EXCLUDED_EXT:
        return True

    return False


def clean_path(path: str, workspace_path: str = "/workspace") -> str:
    """清理并规范化路径，使其相对于工作区

    参数：
        path: 要清理的路径
        workspace_path: 要移除的基础工作区路径（默认："/workspace"）

    返回：
        清理后的路径，相对于工作区
    """
    # 移除前导斜杠
    path = path.lstrip("/")

    # 如果存在工作区前缀则移除
    if path.startswith(workspace_path.lstrip("/")):
        path = path[len(workspace_path.lstrip("/")) :]

    # 如果存在 workspace/ 前缀则移除
    if path.startswith("workspace/"):
        path = path[9:]

    # 移除剩余的前导斜杠
    path = path.lstrip("/")

    return path
