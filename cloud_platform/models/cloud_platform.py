# Copyright 2016-2018 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging
import os
import re

from collections import namedtuple

from distutils.util import strtobool

from odoo import api, models
from odoo.tools.config import config


_logger = logging.getLogger(__name__)


def is_true(strval):
    return bool(strtobool(strval or '0'))


PlatformConfig = namedtuple(
    'PlatformConfig',
    'filestore'
)


class FilestoreKind(object):
    db = 'db'
    s3 = 's3'  # or compatible s3 object storage
    swift = 'swift'
    file = 'file'


class CloudPlatform(models.AbstractModel):
    _name = 'cloud.platform'

    @api.model
    def _platform_kinds(self):
        return []

    @api.model
    def _config_by_server_env(self, platform_kind, environment):
        configs_getter = getattr(
            self,
            '_config_by_server_env_for_%s' % platform_kind,
            None
        )
        configs = configs_getter() if configs_getter else {}
        return configs.get(environment) or FilestoreKind.db

    @api.model
    def install(self, platform_kind):
        assert platform_kind in self._platform_kinds()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('cloud.platform.kind', platform_kind)
        environment = config['running_env']
        configs = self._config_by_server_env(platform_kind, environment)
        params.set_param('ir_attachment.location', configs.filestore)
        self.check()
        if configs.filestore in [FilestoreKind.swift, FilestoreKind.s3]:
            self.env['ir.attachment'].sudo().force_storage()
        _logger.info('cloud platform configured for {}'.format(platform_kind))

    @api.model
    def _check_swift(self, environment_name):
        params = self.env['ir.config_parameter'].sudo()
        use_swift = (params.get_param('ir_attachment.location') ==
                     FilestoreKind.swift)
        if environment_name in ('prod', 'integration'):
            assert use_swift, (
                "Swift must be used on production and integration instances. "
                "It is activated, setting 'ir_attachment.location.' to 'swift'"
                " The 'install_exoscale()' function sets this option "
                "automatically."
            )
        if use_swift:
            assert os.environ.get('SWIFT_AUTH_URL'), (
                "SWIFT_AUTH_URL environment variable is required when "
                "ir_attachment.location is 'swift'."
            )
            assert os.environ.get('SWIFT_ACCOUNT'), (
                "SWIFT_ACCOUNT environment variable is required when "
                "ir_attachment.location is 'swift'."
            )
            assert os.environ.get('SWIFT_PASSWORD'), (
                "SWIFT_PASSWORD environment variable is required when "
                "ir_attachment.location is 'swift'."
            )
            container_name = os.environ['SWIFT_WRITE_CONTAINER']
            if environment_name in ('integration', 'prod'):
                assert container_name, (
                    "SWIFT_WRITE_CONTAINER must not be empty for prod "
                    "and integration"
                )
            prod_container = bool(re.match(r'[a-z0-9-]+-odoo-prod',
                                           container_name))
            if environment_name == 'prod':
                assert prod_container, (
                    "SWIFT_WRITE_CONTAINER should match '<client>-odoo-prod', "
                    "we got: '%s'" % (container_name,)
                )
            else:
                # if we are using the prod bucket on another instance
                # such as an integration, we must be sure to be in read only!
                assert not prod_container, (
                    "SWIFT_WRITE_CONTAINER should not match "
                    "'<client>-odoo-prod', we got: '%s'" % (container_name,)
                )
        elif environment_name == 'test':
            # store in DB so we don't have files local to the host
            assert params.get_param('ir_attachment.location') == 'db', (
                "In test instances, files must be stored in the database with "
                "'ir_attachment.location' set to 'db'. This is "
                "automatically set by the function 'install_ovh()'."
            )

    @api.model
    def _check_s3(self, environment_name):
        params = self.env['ir.config_parameter'].sudo()
        use_s3 = params.get_param('ir_attachment.location') == FilestoreKind.s3
        if environment_name in ('prod', 'integration'):
            assert use_s3, (
                "S3 must be used on production and integration instances. "
                "It is activated by setting 'ir_attachment.location.' to 's3'."
                " The 'install_exoscale()' function sets this option "
                "automatically."
            )
        if use_s3:
            assert os.environ.get('AWS_ACCESS_KEY_ID'), (
                "AWS_ACCESS_KEY_ID environment variable is required when "
                "ir_attachment.location is 's3'."
            )
            assert os.environ.get('AWS_SECRET_ACCESS_KEY'), (
                "AWS_SECRET_ACCESS_KEY environment variable is required when "
                "ir_attachment.location is 's3'."
            )
            assert os.environ.get('AWS_BUCKETNAME'), (
                "AWS_BUCKETNAME environment variable is required when "
                "ir_attachment.location is 's3'.\n"
                "Normally, 's3' is activated on integration and production, "
                "but should not be used in dev environment (or at least "
                "not with a dev bucket, but never the "
                "integration/prod bucket)."
            )
            bucket_name = os.environ['AWS_BUCKETNAME']
            if environment_name in ('integration', 'prod'):
                assert bucket_name, (
                    "AWS_BUCKETNAME must not be empty for prod "
                    "and integration"
                )
            prod_bucket = bool(re.match(r'[a-z-0-9]+-odoo-prod', bucket_name))
            if environment_name == 'prod':
                assert prod_bucket, (
                    "AWS_BUCKETNAME should match '<client>-odoo-prod', "
                    "we got: '%s'" % (bucket_name,)
                )
            else:
                # if we are using the prod bucket on another instance
                # such as an integration, we must be sure to be in read only!
                assert not prod_bucket, (
                    "AWS_BUCKETNAME should not match '<client>-odoo-prod', "
                    "we got: '%s'" % (bucket_name,)
                )

        elif environment_name == 'test':
            # store in DB so we don't have files local to the host
            assert params.get_param('ir_attachment.location') == 'db', (
                "In test instances, files must be stored in the database with "
                "'ir_attachment.location' set to 'db'. This is "
                "automatically set by the function 'install_exoscale()'."
            )

    @api.model
    def _check_redis(self, environment_name):
        if environment_name in ('prod', 'integration', 'test'):
            assert is_true(os.environ.get('ODOO_SESSION_REDIS')), (
                "Redis must be activated on prod, integration, test instances."
                "This is done by setting ODOO_SESSION_REDIS=1."
            )
            assert (os.environ.get('ODOO_SESSION_REDIS_HOST') or
                    os.environ.get('ODOO_SESSION_REDIS_SENTINEL_HOST')), (
                "ODOO_SESSION_REDIS_HOST or ODOO_SESSION_REDIS_SENTINEL_HOST "
                "environment variable is required to connect on Redis"
            )
            assert os.environ.get('ODOO_SESSION_REDIS_PREFIX'), (
                "ODOO_SESSION_REDIS_PREFIX environment variable is required "
                "to store sessions on Redis"
            )

            prefix = os.environ['ODOO_SESSION_REDIS_PREFIX']
            assert re.match(r'[a-z-0-9]+-odoo-[a-z0-9]+[0-9]*', prefix), (
                "ODOO_SESSION_REDIS_PREFIX must match '<client>-odoo-<env>'"
                ", we got: '%s'" % (prefix,)
            )

    @api.model
    def check(self):
        if is_true(os.environ.get('ODOO_CLOUD_PLATFORM_UNSAFE')):
            _logger.warning(
                "cloud platform checks disabled, this is not safe"
            )
            return
        params = self.env['ir.config_parameter'].sudo()
        kind = params.get_param('cloud.platform.kind')
        if not kind:
            _logger.warning(
                "cloud platform not configured, you should "
                "probably run 'env['cloud.platform'].install_exoscale()'"
            )
            return
        environment_name = config['running_env']
        if kind == 'exoscale':
            self._check_s3(environment_name)
        elif kind == 'ovh':
            self._check_swift(environment_name)
        self._check_redis(environment_name)

    def _register_hook(self):
        super(CloudPlatform, self)._register_hook()
        self.sudo().check()
