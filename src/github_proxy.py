"""Proxy for interacting with Github."""

import re

import boto3
from github import Github

import config
import lambdalogging

LOG = lambdalogging.getLogger(__name__)

SAR_APP_URL = ('https://serverlessrepo.aws.amazon.com/applications/arn:aws:serverlessrepo:us-east-1:277187709615:'
               'applications~github-codebuild-logs')
SAR_HOMEPAGE = 'https://aws.amazon.com/serverless/serverlessrepo/'

PR_COMMENT_TEMPLATE = f"""
### AWS CodeBuild CI Report

* CodeBuild project: {{project_name}}
* Commit ID: {{commit_id}}
* Result: {{build_status}}
* [Build Logs]({{logs_url}}) (available for {config.EXPIRATION_IN_DAYS} days)

*Powered by [github-codebuild-logs]({SAR_APP_URL}),\
 available on the [AWS Serverless Application Repository]({SAR_HOMEPAGE})*
"""

CODEBUILD = boto3.client('codebuild')
SECRETS_MANAGER = boto3.client('secretsmanager')


class GithubProxy:
    """Encapsulate interactions with Github."""

    def __init__(self):
        """Initialize proxy."""
        pass

    def publish_pr_comment(self, build):
        """Publish PR comment with link to build logs."""
        pr_comment = PR_COMMENT_TEMPLATE.format(
            project_name=config.PROJECT_NAME,
            commit_id=build.commit_id,
            build_status=build.status,
            logs_url=build.get_logs_url(),
        )

        # initialize client before logging to ensure GitHub attributes are populated
        gh_client = self._get_client()
        LOG.debug('Publishing PR Comment: repo=%s/%s, pr_id=%s, comment=%s',
                  self._github_owner, self._github_repo, build.get_pr_id(), pr_comment)

        repo = gh_client.get_user(self._github_owner).get_repo(self._github_repo)
        repo.get_pull(build.get_pr_id()).create_issue_comment(pr_comment)

    def _get_client(self):
        if not hasattr(self, '_client'):
            self._init_client()
        return self._client

    def _init_client(self):
        self._init_github_info()
        self._client = Github(self._github_token)

    def _init_github_info(self):
        response = CODEBUILD.batch_get_projects(
            names=[config.PROJECT_NAME]
        )

        project_details = response['projects'][0]
        if project_details['source']['type'] != 'GITHUB':
            raise RuntimeError(
                'AWS CodeBuild project {} source is not GITHUB. Project source must be of type GITHUB'.format(
                    config.PROJECT_NAME))

        # if user provided an OAuth token to use, fetch it from secrets manager
        if config.GITHUB_OAUTH_TOKEN_SECRET_ARN:
            secret_response = SECRETS_MANAGER.get_secret_value(SecretId=config.GITHUB_OAUTH_TOKEN_SECRET_ARN)
            self._github_token = secret_response['SecretString']
        # if user did not provide an OAuth token to use, try to get one from the CodeBuild project
        elif project_details['source'].get('auth', {}).get('type') == 'OAUTH':
            self._github_token = project_details['source']['auth']['resource']
        else:
            raise RuntimeError(
                'Could not get GitHub OAuth token from AWS CodeBuild project {}. Please use the GitHubOAuthToken app'
                ' parameter to specify a token to use when writing to GitHub.'.format(config.PROJECT_NAME))

        github_location = project_details['source']['location']
        matches = re.search(r'github\.com\/(.+)\/(.+)\.git$', github_location)
        if not matches:
            raise RuntimeError(
                'Could not parse GitHub owner/repo name from AWS CodeBuild project {}. location={}'.format(
                    config.PROJECT_NAME, github_location))

        self._github_owner = matches.group(1)
        self._github_repo = matches.group(2)
