import demistomock as demisto  # noqa: F401
from CommonServerPython import *  # noqa: F401
from CommonServerUserPython import *  # noqa
from dateutil import parser
from typing import Callable

''' CONSTANTS '''

VENDOR = 'cisco'
PRODUCT = 'webex'
URL = 'https://webexapis.com/v1/'
SCOPE = {
    'admin': 'audit:events_read spark:kms',
    'compliance_officer': 'spark-compliance:events_read spark:kms',
}
COMMAND_FUNCTION_TO_EVENT_TYPE = {
    'get_admin_audits': 'Admin Audit Events',
    'get_security_audits': 'Security Audit Events',
    'get_compliance_officer_events': 'Events',
}
DEFAULT_MAX_FETCH = 200

''' HELPER FUNCTIONS '''


def date_time_to_iso_format(date_time: datetime) -> str:
    """
    Gets a datetime object and returns s string represents a datetime is ISO format. 
    Args:
        date_time: A datetime object.

    Returns:
        A string represents a datetime is ISO format.
    """
    return f'{date_time.isoformat(timespec="milliseconds")}Z'


def create_last_run() -> dict:
    """
    Creates a dict fetch data (last event datetime, next link) for each event type.
    Returns:
        A dict used in the fetch events command for the first fetch.
    """
    start_fetch = datetime.utcnow() - timedelta(weeks=1)
    return {
        'admin_audits': {'since_datetime': date_time_to_iso_format(start_fetch), 'next_url': ''},
        'security_audits': {'since_datetime': date_time_to_iso_format(start_fetch), 'next_url': ''},
        'compliance_officer_events': {'since_datetime': date_time_to_iso_format(start_fetch), 'next_url': ''},
    }


def add_fields_to_events(events: list[dict], evnet_type: str | None):
    """
    Gets a list of events of a specific event type and adds the `_time` & `source_log_type` fields to the event.
    Args:
        events: A list of events.
        evnet_type: The event type.
    """
    for event in events:
        event['_time'] = event.get('created')
        event['source_log_type'] = evnet_type


def increase_datetime_for_next_fetch(events: list) -> str:
    """
    Gets a list of events and returns the latest event create time + a timedelta of a millisecond using for the next fetch.
    Args:
        events: A list of events.

    Returns:
        A string represents a datetime is ISO format.
    """
    return date_time_to_iso_format(parser.parse(events[-1].get('created'), ignoretz=True) + timedelta(milliseconds=1))


''' CLIENT CLASS '''


class Client(BaseClient):
    """Client class to interact with the service API"""

    def __init__(self, url: str, verify: bool, proxy: bool, client_id: str, client_secret: str, redirect_uri: str,
                 scope: str | None, user: str):
        super().__init__(base_url=url, verify=verify, proxy=proxy)
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.user = user

    def create_access_token(self, grant_type: str, code: str | None = None, refresh_token: str | None = None) -> dict:
        """
        Generates a refresh & and access tokens.
        Args:
            grant_type: the grant_type could be either `authorization_code` or `refresh_token`.
            refresh_token: the `refresh_token` to generate the `access_token` when expires.
            code: string returns as a query parameter from the `!cisco-webex-oauth-start` command.
        """
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        params = assign_params(
            grant_type=grant_type,
            code=code,
            refresh_token=refresh_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
        )
        return self._http_request(method='POST', url_suffix='access_token', headers=headers, params=params)

    def save_tokens_to_integration_context(self, result: dict):
        """
        Saves the access & refresh tokens in the integration context.
        Args:
            result: The API json response from the `create_access_token` method.
        """
        now = datetime.utcnow()
        context = assign_params(
            access_token=result.get('access_token'),
            access_token_expires_in=date_time_to_iso_format(now + timedelta(seconds=result.get('expires_in', 0))),
            refresh_token=result.get('refresh_token'),
            refresh_token_expires_in=date_time_to_iso_format(
                now + timedelta(seconds=result.get('refresh_token_expires_in', 0))
            ),
        )
        integration_context = get_integration_context()
        integration_context[self.user] = context
        set_integration_context(integration_context)

    def get_access_token(self) -> str | None:
        """
        Returns the access token from the integration context or generates a new one using the refresh_token.
        Returns:
            The access token or None if the integration context is not set yet.
        """
        if user_integration_context := get_integration_context().get(self.user):
            if datetime.utcnow() > parser.parse(user_integration_context.get('refresh_token_expires_in'), ignoretz=True):
                # In case the refresh token expired we should generate a new one using the !cisco-webex-oauth-start command.
                raise DemistoException('The `refresh token` expired, please re-run the `!cisco-webex-oauth-start` command '
                                       f'with the `user` argument set to {self.user}.')

            if datetime.utcnow() > parser.parse(user_integration_context.get('access_token_expires_in'), ignoretz=True):
                # In case the access token expired we create a new access token using the refresh token.
                result = self.create_access_token('refresh_token', refresh_token=user_integration_context.get('refresh_token'))
                self.save_tokens_to_integration_context(result)
                return result.get('access_token')  # Return the new access token from the API response.

            return user_integration_context.get('access_token')  # Return the access token from the integration context.

        return None

    def oauth_start(self) -> str:
        """returns a URL as a string to use in the oauth start command."""
        params = assign_params(
            response_type='code',
            scope=self.scope,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
        )
        return f'{urljoin(URL, "authorize?")}{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}'

    def oauth_complete(self, code: str | None):
        """
        Completes the authentication process.
        It gets a code returned from the `oauth_start` command and sets the access & refresh token.
        Args:
            code: The code return from the `oauth_start` command as a query parameter.
        """
        result = self.create_access_token('authorization_code', code=code)
        self.save_tokens_to_integration_context(result)

    @abstractmethod
    def oauth_test(self) -> str:
        """
        Abstract function to test the client connection with the API.
        """


class AdminClient(Client):
    def __init__(self, url: str, verify: bool, proxy: bool, client_id: str, client_secret: str, redirect_uri: str,
                 scope: str | None,
                 org_id: str):
        super().__init__(url, verify, proxy, client_id, client_secret, redirect_uri, scope, user='admin')
        self.org_id = org_id
        self._headers = {
            'Authorization': f'Bearer {self.get_access_token()}'
        }

    def oauth_test(self):
        """Runs the `get_admin_audits` method in order to tes the connection."""
        self.get_admin_audits(date_time_to_iso_format(datetime.utcnow() - timedelta(hours=3)))

    def get_admin_audits(self, from_date: str, limit: int = DEFAULT_MAX_FETCH, next_url: str = '') -> requests.Response:
        """
        Returns admin audit events either with a `next_url` or according to filter parameters.
        Args:
            from_date: A string represents a datetime is ISO format from when to get the events.
            limit: A number of how mny events to return.
            next_url: A URL (returned from the previous run) to get the events (using for pagination).

        Returns:
            A response object with the events returned dform the API.
        """
        if next_url:
            return self._http_request(method='GET', full_url=next_url, resp_type='response')
        params = {
            'orgId': self.org_id,
            'from': from_date,
            'to': date_time_to_iso_format(datetime.utcnow()),
            'max': min(limit, DEFAULT_MAX_FETCH),
        }
        return self._http_request(method='GET', url_suffix='adminAudit/events', params=params, resp_type='response')

    def get_security_audits(self, from_date: str, limit: int = DEFAULT_MAX_FETCH, next_url: str = '') -> requests.Response:
        """
        Returns admin security events either with a `next_url` or according to filter parameters.
        Args:
            from_date: A string represents a datetime is ISO format from when to get the events.
            limit: A number of how mny events to return.
            next_url: A URL (returned from the previous run) to get the events (using for pagination).

        Returns:
            A response object with the events returned dform the API.
        """
        if next_url:
            return self._http_request(method='GET', full_url=next_url, resp_type='response')
        params = {
            'orgId': self.org_id,
            'startTime': from_date,
            'endTime': date_time_to_iso_format(datetime.utcnow()),
            'max': min(limit, 1000),
        }
        return self._http_request(method='GET', url_suffix='admin/securityAudit/events', params=params, resp_type='response')


class ComplianceOfficerClient(Client):
    def __init__(self, url: str, verify: bool, proxy: bool, client_id: str, client_secret: str, redirect_uri: str,
                 scope: str | None):
        super().__init__(url, verify, proxy, client_id, client_secret, redirect_uri, scope, user='compliance_officer')
        self._headers = {
            'Authorization': f'Bearer {self.get_access_token()}'
        }

    def oauth_test(self):
        """Runs the `get_compliance_officer_events` method in order to tes the connection."""
        self.get_compliance_officer_events(date_time_to_iso_format(datetime.utcnow() - timedelta(hours=3)))

    def get_compliance_officer_events(self, from_date: str, limit: int = DEFAULT_MAX_FETCH,
                                      next_url: str = '') -> requests.Response:
        """
        Returns events either with a `next_url` or according to filter parameters.
        Args:
            from_date: A string represents a datetime is ISO format from when to get the events.
            limit: A number of how mny events to return.
            next_url: A URL (returned from the previous run) to get the events (using for pagination).

        Returns:
            A response object with the events returned dform the API.
        """
        if next_url:
            return self._http_request(method='GET', full_url=next_url, resp_type='response')
        params = {
            'from': from_date,
            'to': date_time_to_iso_format(datetime.utcnow()),
            'max': min(limit, 1000),
        }
        return self._http_request(method='GET', url_suffix='events', params=params, resp_type='response')


''' COMMAND FUNCTIONS '''


def test_module():
    """Raises an exception with the message to run the `!cisco-webex-oauth-start` command."""
    raise DemistoException(
        'In order to authorize the instance, first run the command `!cisco-webex-oauth-start`, '
        'and complete the process in the URL that is returned. You will then be redirected '
        'to the callback URL. Copy the authorization code found in the query parameter '
        '`code`, and paste that value in the command `!cisco-webex-oauth-complete` as an argument to finish '
        'the process. Then you can test it bu running the `!cisco-webex-oauth-test` command.'
    )


def oauth_start(client: Client) -> CommandResults:
    """
    Runs the client `oauth_start` method in order to create a URL to start the authenticate process.
    Args:
        client: A client object either an AdminClient or a ComplianceOfficerClient.

    Returns:
        A CommandResult with a URL generated according to the client attributes to start the authentication.
    """
    url = client.oauth_start()
    return CommandResults(
        readable_output=f'In order to retrieve the authorization code [click here]({url}).\n'
                        'You will retrieve the authorization code provided as a query parameter called `code`, '
                        'and insert it as an argument to the `!cisco-webex-oauth-complete` command.'
    )


def oauth_complete(client: Client, args: dict) -> CommandResults:
    """
    Gets a code returned from the `oath_start` command as a query parameter and creates an access & refresh token to save them in
    the integration_context.
    Args:
        client: A client object either an AdminClient or a ComplianceOfficerClient.
        args: An authorization code provided as a query parameter called `code` returned from the `oauth_start` command.

    Returns:
        A CommandResult with a message that the tokens was created and saved successfully.
    """
    code = args.get('code')
    client.oauth_complete(code)
    return CommandResults(
        readable_output='### Logged in successfully.\n'
                        'A refresh token was saved to the integration context. This token will be used to '
                        'generate a new access token once the current one expires.\n'
                        'In order to complete the test process please run the `!cisco-oauth-test command`.'
    )


def oauth_test(client: Client) -> CommandResults:
    """
    Runs the `oauth_test` command in order to test the connection.
    Args:
        client: A client object either an AdminClient or a ComplianceOfficerClient.

    Returns:
        A CommandResult with a message that the Test succeeded.
    """
    client.oauth_test()
    return CommandResults(
        readable_output='### Test succeeded!'
    )


def get_events_with_pagination(client_function: Callable, from_date: str, limit: int, next_url: str = '') -> tuple[list, str]:
    """
    Returns events with pagination mechanism.
    Args:
        client_function: The function used to return the events (for each event type).
        from_date: A string represents a datetime is ISO format from when to get the events.
        limit: A number of how mny events to return.
        next_url: A URL (returned from the previous run) to get the events (using for pagination).

    Returns:
        A list of events and a string of the next_url (a URL to return events).
    """
    events: list[dict] = []

    response = client_function(from_date, limit, next_url)
    response_json = response.json()
    events.extend(response_json.get('items', []))

    while (next_url := demisto.get(response.links, 'next.url', '')) and len(events) < limit:
        response = client_function(from_date, limit, next_url)
        response_json = response.json()
        events.extend(response_json.get('items', []))

    add_fields_to_events(events, evnet_type=COMMAND_FUNCTION_TO_EVENT_TYPE.get(client_function.__name__))

    return events, next_url


def get_events_command(command_function: Callable, args: dict) -> tuple[CommandResults, list]:
    """
    Returns a list of events
    Args:
        command_function: The function used to return the events (for each event type).
        args: A dict with fetch data.

    Returns:
        A CommandResult with a readable output of the events and a list of the events.
    """
    from_date = args.get('since_datetime', date_time_to_iso_format(datetime.utcnow() - timedelta(hours=3)))
    limit = arg_to_number(args.get('limit', 5)) or DEFAULT_MAX_FETCH

    events, _ = get_events_with_pagination(command_function, from_date=from_date, limit=limit)

    command_results = CommandResults(
        readable_output=tableToMarkdown(COMMAND_FUNCTION_TO_EVENT_TYPE.get(command_function.__name__), events)
    )
    return command_results, events


def fetch_events(admin_client: AdminClient, co_client: ComplianceOfficerClient, last_run: dict,
                 max_fetch: int) -> tuple[list, dict]:
    """
    Fetches three types of events (Admin Audits, Security Audits, Events),
    It fetches from the latest event `create` date or with a `next_url` returned form the previous fetch,
    And saves the latest evnet create date and the `next_url` from the current fetch to use in the next fetch.
    Args:
        admin_client: An instance of the AdminClient.
        co_client: An instance of the ComplianceOfficesClient.
        last_run: A dict with the latest fetch data.
        max_fetch: A number of how many events to return per fetch.

    Returns:
        A list of events and a dict with fetch info to use in the next fetch.
    """
    all_events = []

    if not last_run:
        last_run = create_last_run()

    demisto.debug(f'start fetching events with last_run: {last_run}')

    event_type_to_client_function = {
        'admin_audits': admin_client.get_admin_audits,
        'security_audits': admin_client.get_security_audits,
        'compliance_officer_events': co_client.get_compliance_officer_events,
    }

    for event_type, client_function in event_type_to_client_function.items():
        since_datetime = demisto.get(last_run, f'{event_type}.since_datetime')
        next_url = demisto.get(last_run, f'{event_type}.next_url', '')
        events, next_url = get_events_with_pagination(client_function, since_datetime, max_fetch, next_url)
        last_run[event_type]['next_url'] = next_url
        if events:
            last_run[event_type]['since_datetime'] = increase_datetime_for_next_fetch(events)
            all_events.extend(events)

    demisto.debug(f'finished fetching {len(all_events)} events, last_run will be set to: {last_run}')

    return all_events, last_run


''' MAIN FUNCTION '''


def main() -> None:  # pragma: no cover
    """main function, parses params and runs command functions"""

    params = demisto.params()
    args = demisto.args()
    command = demisto.command()

    # parse parameters
    admin_client_id = demisto.get(params, 'admin_credentials.identifier', '')
    admin_client_secret = demisto.get(params, 'admin_credentials.password', '')
    admin_redirect_uri = params.get('admin_app_redirect_uri')
    admin_org_id = params.get('admin_org_id')
    compliance_officer_client_id = demisto.get(params, 'compliance_officer_credentials.identifier', '')
    compliance_officer_client_secret = demisto.get(params, 'compliance_officer_credentials.password', '')
    compliance_officer_redirect_uri = params.get('compliance_officer_redirect_uri')
    verify_certificate = not params.get("insecure", False)
    proxy = params.get("proxy", False)
    max_fetch = arg_to_number(args.get('max_fetch', DEFAULT_MAX_FETCH)) or DEFAULT_MAX_FETCH
    if not 0 < max_fetch <= 2000:
        max_fetch = DEFAULT_MAX_FETCH

    demisto.debug(f'Command being called is {demisto.command()}')

    try:
        admin_client = AdminClient(
            url=URL,
            verify=verify_certificate,
            proxy=proxy,
            client_id=admin_client_id,
            client_secret=admin_client_secret,
            redirect_uri=admin_redirect_uri,
            org_id=admin_org_id,
            scope=SCOPE.get('admin'),
        )

        compliance_officer_client = ComplianceOfficerClient(
            url=URL,
            verify=verify_certificate,
            proxy=proxy,
            client_id=compliance_officer_client_id,
            client_secret=compliance_officer_client_secret,
            redirect_uri=compliance_officer_redirect_uri,
            scope=SCOPE.get('compliance_officer'),
        )

        if demisto.command() == 'test-module':
            test_module()

        elif demisto.command() == 'cisco-webex-oauth-start':
            client = admin_client if args.get('user') == 'admin' else compliance_officer_client
            result = oauth_start(client)
            return_results(result)

        elif demisto.command() == 'cisco-webex-oauth-complete':
            client = admin_client if args.get('user') == 'admin' else compliance_officer_client
            result = oauth_complete(client, args)
            return_results(result)

        elif demisto.command() == 'cisco-webex-oauth-test':
            client = admin_client if args.get('user') == 'admin' else compliance_officer_client
            result = oauth_test(client)
            return_results(result)

        elif command == 'cisco-webex-get-admin-audit-events':
            command_results, events = get_events_command(admin_client.get_admin_audits, args)
            return_results(command_results)
            if argToBoolean(args.get('should_push_events', False)):
                send_events_to_xsiam(events, vendor=VENDOR, product=PRODUCT)

        elif command == 'cisco-webex-get-security-audit-events':
            command_results, events = get_events_command(admin_client.get_security_audits, args)
            return_results(command_results)
            if argToBoolean(args.get('should_push_events', False)):
                send_events_to_xsiam(events, vendor=VENDOR, product=PRODUCT)

        elif command == 'cisco-webex-get-compliance-officer-events':
            command_results, events = get_events_command(compliance_officer_client.get_compliance_officer_events, args)
            return_results(command_results)
            if argToBoolean(args.get('should_push_events', False)):
                send_events_to_xsiam(events, vendor=VENDOR, product=PRODUCT)

        elif command == 'fetch-events':
            last_run = demisto.getLastRun()
            events, next_run = fetch_events(admin_client, compliance_officer_client, last_run, max_fetch)
            send_events_to_xsiam(events, vendor=VENDOR, product=PRODUCT)
            demisto.setLastRun(next_run)

    # Log exceptions and return errors
    except Exception as e:
        return_error(f'Failed to execute {demisto.command()} command.\nError:\n{str(e)}')


''' ENTRY POINT '''

if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
