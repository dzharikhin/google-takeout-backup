def sanitize_cookies(cookies):
    sanitized = []
    for cookie in cookies:
        cookie_copy = dict(cookie)
        if cookie_copy.get("sameSite") == "None" and not cookie_copy.get("secure"):
            del cookie_copy["sameSite"]
        sanitized.append(cookie_copy)
    return sanitized
