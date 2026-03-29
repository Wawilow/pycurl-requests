from collections import OrderedDict
from typing import Generator, Optional

import pycurl
import os

from pycurl_requests import adapters, structures
from pycurl_requests.auth import CurlAuth, HTTPBasicAuth
from pycurl_requests.cookies import RequestsCookieJar
from pycurl_requests.exceptions import InvalidSchema
from pycurl_requests.models import (
    DEFAULT_REDIRECT_LIMIT,
    PreparedRequest,
    Request,
    Response,
)

from .utils import Mapping, to_key_val_list, get_environ_proxies


# Stubbed out for Requests tests
class SessionRedirectMixin:
    pass


class Session:
    def __init__(self):
        self.auth = None
        self.cert = None
        self.cookies = RequestsCookieJar()
        self.headers = structures.CaseInsensitiveDict()
        self.hooks = NotImplemented
        self.max_redirects = DEFAULT_REDIRECT_LIMIT
        self.params = {}
        self.proxies = {}
        self.stream = False
        self.trust_env = True
        self.verify = True

        self.curl = pycurl.Curl()

        self.adapters = OrderedDict()
        self.mount("https://", adapters.PyCurlHttpAdapter(self.curl))
        self.mount("http://", adapters.PyCurlHttpAdapter(self.curl))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if self.curl:
            self.curl.close()

        self.curl = None

    def get(self, url, params=None, **kwargs) -> Response:
        return self.request("GET", url, params=params, **kwargs)

    def head(self, url, **kwargs) -> Response:
        return self.request("HEAD", url, **kwargs)

    def options(self, url, **kwargs) -> Response:
        return self.request("OPTIONS", url, **kwargs)

    def post(self, url, data=None, json=None, **kwargs) -> Response:
        return self.request("POST", url, data=data, json=json, **kwargs)

    def put(self, url, data=None, **kwargs) -> Response:
        return self.request("PUT", url, data=data, **kwargs)

    def patch(self, url, data=None, **kwargs) -> Response:
        return self.request("PATCH", url, data=data, **kwargs)

    def delete(self, url, **kwargs) -> Response:
        return self.request("DELETE", url, **kwargs)

    def request(
        self,
        method,
        url,
        params=None,
        data=None,
        headers=None,
        cookies=None,
        files=None,
        auth=None,
        timeout=None,
        allow_redirects=True,
        proxies=None,
        hooks=None,
        stream=None,
        verify=None,
        cert=None,
        json=None,
    ) -> Response:
        request = Request(
            method,
            url,
            params=params,
            data=data,
            json=json,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            hooks=hooks,
        )

        prepared = self.prepare_request(request)

        settings = dict(
            timeout=timeout,
            allow_redirects=allow_redirects,
            max_redirects=self.max_redirects,
        )
        settings.update(
            self.merge_environment_settings(prepared.url, proxies, stream, verify, cert)
        )

        return self.send(prepared, **settings)

    def get_adapter(self, url) -> adapters.BaseAdapter:
        for prefix, adapter in self.adapters.items():
            if url.lower().startswith(prefix.lower()):
                return adapter

        raise InvalidSchema(f"No connection adapters were found for {url!r}")

    def get_redirect_target(self, resp: Response) -> Optional[str]:
        raise NotImplementedError


def merge_setting(request_setting, session_setting, dict_class=OrderedDict):
    """Determines appropriate setting for a given request, taking into account
    the explicit setting on that request, and the setting in the session. If a
    setting is a dictionary, they will be merged together using `dict_class`
    """

    if session_setting is None:
        return request_setting

    if request_setting is None:
        return session_setting

    # Bypass if not a dictionary (e.g. verify)
    if not (
        isinstance(session_setting, Mapping) and isinstance(request_setting, Mapping)
    ):
        return request_setting

    merged_setting = dict_class(to_key_val_list(session_setting))
    merged_setting.update(to_key_val_list(request_setting))

    # Remove keys that are set to None. Extract keys first to avoid altering
    # the dictionary during iteration.
    none_keys = [k for (k, v) in merged_setting.items() if v is None]
    for key in none_keys:
        del merged_setting[key]

    return merged_setting

    def merge_environment_settings(self, url, proxies, stream, verify, cert) -> dict:
        """
        Check the environment and merge it with some settings.

        :rtype: dict
        """
        # Gather clues from the surrounding environment.
        if self.trust_env:
            # Set environment's proxies.
            no_proxy = proxies.get("no_proxy") if proxies is not None else None
            env_proxies = get_environ_proxies(url, no_proxy=no_proxy)
            for k, v in env_proxies.items():
                proxies.setdefault(k, v)

            # Look for requests environment configuration
            # and be compatible with cURL.
            if verify is True or verify is None:
                verify = (
                    os.environ.get("REQUESTS_CA_BUNDLE")
                    or os.environ.get("CURL_CA_BUNDLE")
                    or verify
                )

        # Merge all the kwargs.
        proxies = merge_setting(proxies, self.proxies)
        stream = merge_setting(stream, self.stream)
        verify = merge_setting(verify, self.verify)
        cert = merge_setting(cert, self.cert)

        return {"proxies": proxies, "stream": stream, "verify": verify, "cert": cert}

    def mount(self, prefix, adapter):
        """
        Registers a connection adapter to a prefix.

        Adapters are sorted in descending order by prefix length.
        """
        self.adapters[prefix] = adapter
        keys_to_move = [k for k in self.adapters if len(k) < len(prefix)]

        for key in keys_to_move:
            self.adapters[key] = self.adapters.pop(key)

    def prepare_request(self, request: Request) -> PreparedRequest:
        prepared = PreparedRequest()

        headers = structures.CaseInsensitiveDict()
        for name, value in self.headers.items():
            headers[name] = value
        for name, value in (request.headers or {}).items():
            headers[name] = value

        prepared.prepare(
            method=request.method,
            url=request.url,
            headers=headers,
            files=request.files,
            data=request.data,
            json=request.json,
            params=_merge_params(self.params, request.params),
            auth=request.auth or self.auth,
            cookies=_merge_params(self.cookies, request.cookies),
            hooks=NotImplemented,
        )  # TODO: Merge request with Session

        return prepared

    def rebuild_auth(self, prepared_request, response):
        raise NotImplementedError

    def rebuild_method(self, prepared_request, response):
        raise NotImplementedError

    def rebuild_proxies(self, prepared_request, proxies) -> dict:
        raise NotImplementedError

    def resolve_redirects(
        self,
        resp: Response,
        req: PreparedRequest,
        stream=False,
        timeout=None,
        verify=True,
        cert=None,
        proxies=None,
        yield_requests=False,
        **adapter_kwargs,
    ) -> Generator:
        raise NotImplementedError

    def send(self, request: PreparedRequest, **kwargs):
        adapter = self.get_adapter(request.url)

        return adapter.send(request, **kwargs)

    def should_strip_auth(self, old_url, new_url):
        raise NotImplementedError


def _merge_params(current, new):
    """Merge parameters dictionary"""
    if not new:
        return current

    if not current:
        return new

    current = current.copy()
    current.update(new)

    return current


def session():
    """
    Create a Session

    .. deprecated:: 1.0.0
        Use :class:`~pycurl_requests.sessions.Session` instead.
    """
    return Session()
