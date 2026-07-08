"""URL/Email 扫描函数（字符级，无正则）。"""

from __future__ import annotations


def _scan_url_end(text: str, start: int) -> int:
    """从 start 开始扫描 URL 结尾位置，支持括号平衡。

    扫描直到遇到空格或排除字符（< > " ' [ ] { } ， 。 、 ！ ？ ； ： 】 》 》 ） — – —），
    同时处理以下情况：
      - 逗号/句号后紧跟空格 → 截断（避免吞噬句末标点）
      - 追踪括号平衡，扫描结束后剥离多余右括号
      - 遇到右括号但括号未开 → 截断包含但会在 trim 阶段处理

    Args:
        text: 完整文本
        start: 扫描起始位置

    Returns:
        url_end: URL 结束位置（不含后续标点/空格）
    """
    n = len(text)
    url_end = start

    # 第一遍：收集字符（允许 () 括号，后续通过平衡逻辑处理多余右括号）
    while (url_end < n
           and not text[url_end].isspace()
           and text[url_end] not in '<>"\'[]{},，。、！？；：】》》）—–—'):
        if (text[url_end] in ',.!?;:'
                and url_end + 1 < n
                and text[url_end + 1].isspace()):
            break
        url_end += 1

    # 第二遍：检查括号平衡并剥离多余右括号
    url_text = text[start:url_end]
    # 统计开括号和闭括号数量
    open_count = url_text.count('(')
    close_count = url_text.count(')')
    # 如果闭括号多于开括号，从末尾逐个剥离多余的
    while url_text and close_count > open_count:
        if url_text[-1] == ')':
            url_text = url_text[:-1]
            url_end -= 1
            close_count -= 1
        elif url_text[-1] in '.,;:!?\'"':
            url_text = url_text[:-1]
            url_end -= 1
        else:
            break

    # 第三遍：剥离尾部常规标点（逗号/句号/分号/冒号/感叹号/问号/引号）
    # 注意：不剥离 `)`，因为它已在前一步处理
    while url_text and url_text[-1] in '.,;:!?\'"':
        url_text = url_text[:-1]
        url_end -= 1

    return url_end


def _scan_next_url(text: str, start: int = 0) -> tuple[int, int, str] | None:
    """从 start 位置开始扫描下一个裸 URL。

    返回 (start_pos, end_pos, url_text) 或 None。

    优化：使用 str.find 跳跃定位可能的协议/域名位置，
    避免逐字符扫描整个文本。
    """
    protocols = ('https://', 'http://', 'ftp://', 'ftps://')
    n = len(text)

    if start >= n:
        return None

    # ★ 优化：预计算一次小写文本，复用避免多次 text.lower() 调用
    # 只计算 start 之后的部分，减少内存和计算开销
    remaining = text[start:]
    lower_remaining = remaining.lower()

    i = start
    while i < n:
        # ★ 优化：用 str.find 快速跳跃到下一个 : 或 www 位置
        # find 在 CPython 中由 C 级 memchr 实现，比逐字符循环快 10-100 倍
        slice_from = i

        # 查找下一个冒号位置
        next_colon = text.find(':', slice_from)

        # 查找下一个 www. 位置（在 lower_remaining 中查找，索引需偏移）
        next_www = lower_remaining.find('www.', slice_from - start)
        if next_www >= 0:
            next_www += start  # 转换为绝对位置

        # 没有候选位置 → 退出
        if next_colon == -1 and next_www == -1:
            break

        # 选择最近的候选位置
        candidates = []
        if next_colon >= 0 and next_colon >= i:
            candidates.append(next_colon)
        if next_www >= 0 and next_www >= i:
            candidates.append(next_www)

        if not candidates:
            break

        i = min(candidates)

        # ── 协议检测 ──
        matched_protocol = False
        for protocol in protocols:
            plen = len(protocol)
            colon_offset = plen - 3  # "://" 占3字符
            proto_start = i - colon_offset
            if (proto_start >= 0 and proto_start + plen <= n
                    and text[proto_start:proto_start + plen].lower() == protocol):
                url_end = _scan_url_end(text, proto_start + plen)
                url_text = text[proto_start:url_end]
                # 剥离尾部常规标点
                while url_text and url_text[-1] in '.,;:!?\'"':
                    url_text = url_text[:-1]
                    url_end -= 1
                if len(url_text) > plen:
                    return (proto_start, url_end, url_text)
                matched_protocol = True
                break

        if matched_protocol:
            i += 1
            continue

        # ── www. 裸域名检测 ──
        _www = 'www.'
        if (i + len(_www) <= n
                and text[i:i + len(_www)].lower() == _www):
            url_end = _scan_url_end(text, i + len(_www))
            url = text[i:url_end]
            # 剥离尾部常规标点
            while url and url[-1] in '.,;:!?\'"':
                url = url[:-1]
                url_end -= 1
            dot_count = 0
            for ch in url:
                if ch == '.':
                    dot_count += 1
            if len(url) > len(_www) and dot_count >= 1:
                return (i, url_end, 'http://' + url)

        i += 1  # 跳过当前位置，继续查找下一个

    return None


def _scan_next_email(text: str, start: int = 0) -> tuple[int, int, str] | None:
    """从 start 位置开始扫描下一个 Email 地址。

    返回 (start_pos, end_pos, email_text) 或 None。
    参考 recursive_parser.py 中 _try_bare_email() 的实现。
    """
    n = len(text)
    i = start
    while i < n:
        # 【优化】快速跳转：用 find 跳到下一个 @ 位置
        # 对于纯文本，跳过整个无 @ 的区域，避免逐字符扫描
        at_pos = text.find('@', i)
        if at_pos == -1:
            break

        i = at_pos
        local_start = i
        while local_start > start and (text[local_start - 1].isalnum()
                                        or text[local_start - 1] in '._%+-'):
            local_start -= 1
        if local_start < i:
            domain_start = i + 1
            domain_end = domain_start
            while (domain_end < n
                   and (text[domain_end].isalnum()
                        or text[domain_end] in '.-')):
                domain_end += 1
            if domain_end - domain_start >= 3 and '.' in text[domain_start:domain_end]:
                email = text[local_start:domain_end]
                while email and email[-1] in '.,;:!?)\'"':
                    email = email[:-1]
                    domain_end -= 1
                if '@' in email and '.' in email[email.index('@') + 1:]:
                    return (local_start, domain_end, email)
        i += 1
    return None


def _scan_next_url_or_email(text: str, start: int = 0) -> tuple[int, int, str, str] | None:
    """从 start 位置开始扫描下一个裸 URL 或 Email 地址（合并扫描）。

    一次扫描同时查找 URL 和 Email，返回最先出现的结果。
    消除 inline_renderer 中先扫 URL 再扫 Email 的两次扫描开销。

    Returns:
        (start_pos, end_pos, text, type)
        type='url' 表示 URL，type='email' 表示 Email
        未找到时返回 None
    """
    # 先用 str.find 快速定位候选位置
    n = len(text)
    if start >= n:
        return None
    
    remaining = text[start:]
    lower_remaining = remaining.lower()
    
    i = start
    while i < n:
        # 找下一个 :（协议候选）、www.（域名候选）和 @（Email 候选）
        next_colon = text.find(':', i)
        next_www = lower_remaining.find('www.', i - start)
        if next_www >= 0:
            next_www += start
        next_at = text.find('@', i)
        
        # 都没有 → 结束
        if next_colon == -1 and next_www == -1 and next_at == -1:
            break
        
        # 取最近候选
        candidates = []
        if next_colon >= 0 and next_colon >= i:
            candidates.append((next_colon, 'colon'))
        if next_www >= 0 and next_www >= i:
            candidates.append((next_www, 'www'))
        if next_at >= 0 and next_at >= i:
            candidates.append((next_at, 'at'))
        
        if not candidates:
            break
        
        candidates.sort(key=lambda x: x[0])
        pos, kind = candidates[0]
        i = pos
        
        # URL 协议检测（':' 前推）
        if kind == 'colon':
            protocols = ('https://', 'http://', 'ftp://', 'ftps://')
            matched_protocol = False
            for protocol in protocols:
                plen = len(protocol)
                colon_offset = plen - 3
                proto_start = i - colon_offset
                if (proto_start >= 0 and proto_start + plen <= n
                        and text[proto_start:proto_start + plen].lower() == protocol):
                    url_end = _scan_url_end(text, proto_start + plen)
                    url_text = text[proto_start:url_end]
                    if len(url_text) > plen:
                        return (proto_start, url_end, url_text, 'url')
                    matched_protocol = True
                    break
            if matched_protocol:
                i += 1
                continue
        
        # www. 域名检测
        if kind == 'www' or (kind == 'colon' and i + 4 <= n):
            _www = 'www.'
            if i + len(_www) <= n and text[i:i + len(_www)].lower() == _www:
                url_end = _scan_url_end(text, i + len(_www))
                url = text[i:url_end]
                # 剥离尾部常规标点
                while url and url[-1] in '.,;:!?\'"':
                    url = url[:-1]
                    url_end -= 1
                dot_count = 0
                for ch in url:
                    if ch == '.':
                        dot_count += 1
                if len(url) > len(_www) and dot_count >= 1:
                    return (i, url_end, 'http://' + url, 'url')
        
        # Email 检测
        if kind == 'at':
            local_start = i
            local_count = 0
            while local_start > start and local_count < 64:
                if text[local_start - 1].isalnum() or text[local_start - 1] in '._%+-':
                    local_start -= 1
                    local_count += 1
                else:
                    break
            if local_count > 0:
                domain_start = i + 1
                domain_end = domain_start
                while (domain_end < n
                       and (text[domain_end].isalnum()
                            or text[domain_end] in '.-')):
                    domain_end += 1
                if domain_end - domain_start >= 3 and '.' in text[domain_start:domain_end]:
                    email = text[local_start:domain_end]
                    while email and email[-1] in '.,;:!?)\'"':
                        email = email[:-1]
                        domain_end -= 1
                    if '@' in email and '.' in email[email.index('@') + 1:]:
                        return (local_start, domain_end, email, 'email')
        
        i += 1
    
    return None
