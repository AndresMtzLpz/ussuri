#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""WSGI application initialization for Nova APIs."""

import os

from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import _options as service_opts
from paste import deploy
import six

from nova import config
from nova import context
from nova import exception
from nova import objects
from nova import service
from nova import utils

CONF = cfg.CONF

CONFIG_FILES = ['api-paste.ini', 'nova.conf']

LOG = logging.getLogger(__name__)

objects.register_all()


def _get_config_files(env=None):
    if env is None:
        env = os.environ
    dirname = env.get('OS_NOVA_CONFIG_DIR', '/etc/nova').strip()
    return [os.path.join(dirname, config_file)
            for config_file in CONFIG_FILES]


def _setup_service(host, name):
    try:
        utils.raise_if_old_compute()
    except exception.TooOldComputeService as e:
        logging.getLogger(__name__).warning(six.text_type(e))

    binary = name if name.startswith('nova-') else "nova-%s" % name

    ctxt = context.get_admin_context()
    service_ref = objects.Service.get_by_host_and_binary(
        ctxt, host, binary)
    if service_ref:
        service._update_service_ref(service_ref)
    else:
        try:
            service_obj = objects.Service(ctxt)
            service_obj.host = host
            service_obj.binary = binary
            service_obj.topic = None
            service_obj.report_count = 0
            service_obj.create()
        except (exception.ServiceTopicExists,
                exception.ServiceBinaryExists):
            # If we race to create a record with a sibling, don't
            # fail here.
            pass


def error_application(exc, name):
    # TODO(cdent): make this something other than a stub
    def application(environ, start_response):
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/plain; charset=UTF-8')])
        return ['Out of date %s service %s\n' % (name, exc)]
    return application


@utils.run_once('Global data already initialized, not re-initializing.',
                LOG.info)
def init_global_data(conf_files):
    # NOTE(melwitt): parse_args initializes logging and calls global rpc.init()
    # and db_api.configure(). The db_api.configure() call does not initiate any
    # connection to the database.
    config.parse_args([], default_config_files=conf_files)

    logging.setup(CONF, "nova")

    # dump conf at debug (log_options option comes from oslo.service)
    # FIXME(mriedem): This is gross but we don't have a public hook into
    # oslo.service to register these options, so we are doing it manually for
    # now; remove this when we have a hook method into oslo.service.
    CONF.register_opts(service_opts.service_opts)
    if CONF.log_options:
        CONF.log_opt_values(
            logging.getLogger(__name__),
            logging.DEBUG)


def init_application(name):
    conf_files = _get_config_files()

    # NOTE(melwitt): The init_application method can be called multiple times
    # within a single python interpreter instance if any exception is raised
    # during it (example: DBConnectionError while setting up the service) and
    # apache/mod_wsgi reloads the init_application script. So, we initialize
    # global data separately and decorate the method to run only once in a
    # python interpreter instance.
    init_global_data(conf_files)

    try:
        _setup_service(CONF.host, name)
    except exception.ServiceTooOld as exc:
        return error_application(exc, name)

    # This global init is safe because if we got here, we already successfully
    # set up the service and setting up the profile cannot fail.
    service.setup_profiler(name, CONF.host)

    conf = conf_files[0]

    return deploy.loadapp('config:%s' % conf, name=name)
