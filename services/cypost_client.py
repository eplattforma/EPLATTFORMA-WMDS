import requests
import os
import logging
from flask import current_app

class CyprusPostClient:
    def __init__(self):
        self.api_key = os.environ.get("CYPOST_API_KEY")
        self.base_url = os.environ.get("CYPOST_BASE_URL", "https://cypruspost.post/api/postal-codes/")
        self.lng = os.environ.get("CYPOST_LNG", "en")
        self.timeout = 15

    def _get_headers(self):
        return {
            "Authorization": self.api_key,
            "Accept": "application/json"
        }

    def _request(self, endpoint, params=None):
        if not self.api_key:
            raise Exception("CYPOST_API_KEY not configured")
        
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        all_params = {"lng": self.lng}
        if params:
            all_params.update(params)
            
        try:
            response = requests.get(
                url, 
                headers=self._get_headers(), 
                params=all_params, 
                timeout=self.timeout
            )
            if response.status_code != 200:
                logging.error(f"Cyprus Post API Error: {response.status_code} - {response.text}")
                response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Cyprus Post API Connection Error: {str(e)}")
            raise Exception(f"Failed to connect to Cyprus Post API: {str(e)}")

    def districts(self):
        return self._request("district-selection")

    def areas(self, district, page_token=None):
        params = {"district": district}
        if page_token:
            params["page_token"] = page_token
        return self._request("get-areas", params)

    def search(self, district, param, area=None, page_token=None):
        if len(param) < 3:
            return {"results": []}
        params = {"district": district, "param": param}
        if area:
            params["area"] = area
        if page_token:
            params["page_token"] = page_token
        return self._request("search", params)

    def addresses(self, post_code, page_token=None):
        params = {"postal_code": post_code}
        if page_token:
            params["page_token"] = page_token
        return self._request("addresses", params)
