from facebook_business.api import FacebookAdsApi

class FacebookAdsClient:
    def __init__(self, app_id, app_secret, access_token):
        self.api = FacebookAdsApi.init(app_id, app_secret, access_token, api_version='v20.0')