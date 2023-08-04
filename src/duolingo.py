"""
Custom API Client for Duolingo. This is necessary to access your own statistics in your Duolingo account.

Essentially, there are three endpoints that will be used during the lifecycle of this API helper, which are:
- `https://www.duolingo.com/login` -- to log in to the API.
- `https://www.duolingo.com/users/<USERNAME>` -- to access the currently logged in user's data and streak information.
- `https://www.duolingo.com/2017-06-30/users/<UID>/xp_summaries?startDate=1970-01-01` -- to access the currently logged in user's experience gain information.

Please use this code responsibly and do not spam Duolingo's servers by using it like you're a bot or something.

You'll get rate-limited, make their software engineers jobs' harder, and it's not a good thing.
"""

from dataclasses import dataclass
from datetime import datetime
from json import loads, dumps
from typing import Any, Literal, NoReturn, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

import requests


class Summary(BaseModel):
    """
    API response of Duolingo's single summary entry.
    """

    model_config = ConfigDict(populate_by_name=True)

    date: int = Field(alias="date")
    daily_goal_xp: int = Field(alias="dailyGoalXp")
    gained_xp: int = Field(alias="gainedXp")
    num_sessions: int = Field(alias="numSessions")
    total_session_time: int = Field(alias="totalSessionTime")


class SummaryResponse(BaseModel):
    """
    API response of Duolingo summaries.
    """

    summaries: list[Summary]


class UserDataResponse(BaseModel):
    """
    API response of Duolingo streak count.
    """

    site_streak: int


@dataclass
class Duolingo:
    """
    REST API Client for Duolingo API. Please use responsibly and do not spam their servers. When initializing
    this class, please use `kwargs`-style arguments (key-value) rather than just inputting it per parameter. This
    is to ensure an explicit initialization instead of implicit initialization.
    """

    ##
    # Special exceptions relevant to this class to be exported and used by an external party.
    # This is important, as we want to define our own exceptions instead of using the
    # already made ones.
    ##
    class BreakingAPIChange(Exception):
        """
        Special exceptions if the format of the API suddenly change.
        """

    class CaptchaException(Exception):
        """
        Special exception for captcha responses. If this happens, it means that you
        are probably caught in their spam filter and have to change your user agent. You also
        have to log in again.
        """

    class LoginException(Exception):
        """
        Special exception if you failed to log in to the API. This means that your credentials are either wrong,
        or an internal server error happened at Duolingo's API.
        """

    class NotFoundException(Exception):
        """
        Exception that will be thrown if the API returns a `404 Not Found`.
        """

    class UnauthorizedException(Exception):
        """
        Exception that will be thrown if the API returns a `401 Unauthorized`.
        """

    ##
    # Constants, unchanging state of this class.
    ##
    BASE_URL = "https://www.duolingo.com"
    """Base URL of Duolingo's API."""

    ##
    # Class members to be initialized in the `__init__` method. Remember, this is a `@dataclass`. For usage, it is
    # recommended that you treat this like inserting `**kwargs`-style arguments.
    ##
    username: str
    """Your Duolingo's username."""

    password: Optional[str]
    """Your Duolingo's password. Can be superseded by your Duolingo's JSON Web Token if it exists."""

    jwt: Optional[str]
    """Your Duolingo's JSON Web Token. The main token used to authenticate your requests to the API."""

    session = requests.Session()
    """Session of this class instance. Using sessions will be helpful to preserve network footprints."""

    daily_experience_progress: dict[str, Any]
    """Your Duolingo's daily experience progress."""

    user_data: dict[str, Any]
    """Your Duolingo's user data."""

    login_method: Union[Literal["JWT"], Literal["Password"]] = "Password"
    """Method of login used to authenticate yourself at the Duolingo API, by default was set to `Password`, capital letter at the front."""

    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36"
    """A user agent to be used to make requests to the API."""

    ##
    # Methods of this class.
    ##
    def request(
        self,
        url: str,
        data: Optional[dict[str, Any]] = None,
    ) -> requests.Response:
        """
        Used to perform a request / API call to Duolingo's API. Handles all possible errors I could
        think of, with the proper authorization (network headers) and request body.
        """
        # Creates required network headers to perform authenticated requests.
        headers = {
            "Authorization": f"Bearer {self.jwt}" if self.jwt is not None else "",
            "User-Agent": self.user_agent,
        }

        # Prepare request.
        request = requests.Request(
            method="POST" if data else "GET",
            url=url,
            json=data,
            headers=headers,
            cookies=self.session.cookies,
        )

        # Send request.
        response = self.session.send(request.prepare())

        # Handle several errors: `401` and `404`.
        if response.status_code == 401:
            raise self.UnauthorizedException(
                f"You are not authorized to access the resource with URL: '{url}'. Please try again with the correct credentials."
            )
        elif response.status_code == 404:
            raise self.NotFoundException(
                "The resource that you are looking for is not found."
            )

        # Handle an edge case: captcha lock-out!
        if (
            response.status_code == 403
            and response.json().get("blockScript") is not None
        ):
            raise self.CaptchaException(
                f"Request to '{url}' with user agent '{self.user_agent}' was blocked, and the API requests you to solve a captcha. Please try logging in again with a different user agent."
            )

        # Return proper response object.
        return response

    def login(self) -> Union[str, NoReturn]:
        """
        Logs in to the Duolingo API. Steps:
        - If the user does not have a JWT, they will be logged in with their `username` and `password`.
        - Populates the whole `user_data` and `daily_progress` dictionary.

        Please store the JWT (returned from this function) after this function returns. This is
        intentionally done to prevent side-effects, keeping this function as pure as possible.
        """
        # Log in properly if the user does not have any JWT.
        if not self.jwt:
            response = self.request(
                f"{self.BASE_URL}/login",
                data={
                    "login": self.username,
                    "password": self.password,
                },
            )
            if "failure" in response.json():
                raise self.LoginException(
                    "Failed to log in with your current credentials. Please check it and try again later."
                )

            # Inject our JWT for subsequent requests in the same session.
            self.jwt = response.headers["jwt"]
        else:
            # If we log in with JWT, we have to make sure that we set this flag.
            self.login_method = "JWT"

        # Return our JWT.
        return self.jwt

    def fetch_data(self):
        """
        Fetches the user's data from the Duolingo's API. This should be called right after one has logged in. Method
        will perform two API calls.
        """
        self.user_data = self.request(f"{self.BASE_URL}/users/{self.username}").json()
        self.daily_experience_progress = self.request(
            f"{self.BASE_URL}/2017-06-30/users/{self.user_data['id']}/xp_summaries?startDate=1970-01-01"
        ).json()

        return self.user_data, self.daily_experience_progress

    def get_summaries(self):
        """
        Gets the summary of the currently logged in user. We will get the data of the daily goal XP,
        the gained XP for today, number of sessions/lessons that the user has taken for today, and how
        long the user has been using Duolingo for today.

        If the API schema change, then it will throw a validation error. Expected JSON data:

        ```json
        {
            "summaries": [
                {
                    "date": 1659657600,
                    "numSessions": 1,
                    "gainedXp": 100,
                    "frozen": false,
                    "repaired": false,
                    "streakExtended": true,
                    "userId": 1,
                    "dailyGoalXp": 50,
                    "totalSessionTime": 1
                },
                {
                    "date": 1659571200,
                    "numSessions": 1,
                    "gainedXp": 200,
                    "frozen": false,
                    "repaired": false,
                    "streakExtended": true,
                    "userId": 1,
                    "dailyGoalXp": 50,
                    "totalSessionTime": 1
                }
            ]
        }
        ```

        As a note, `summaries` at position `0` will always show the latest time.
        """
        try:
            response = SummaryResponse(**self.daily_experience_progress)
            return response.summaries
        except ValidationError:
            raise self.BreakingAPIChange(
                "API response does not conform to the schema. Perhaps the response from the server may have been changed."
            )

    def get_user_data(self):
        """
        Gets current information about our daily streak from Duolingo. This process is done by querying the `user_data`
        class attribute.

        Expected JSON data (not real data):

        ```json
        {
            "site_streak": 10
        }
        ```
        """
        try:
            response = UserDataResponse(**self.user_data)
            return response
        except ValidationError:
            raise self.BreakingAPIChange(
                "API response does not conform to the schema. Perhaps the response from the server may have been changed."
            )
