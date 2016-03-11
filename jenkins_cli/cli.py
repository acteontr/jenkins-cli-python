from __future__ import print_function
import os
from time import time
import datetime
import jenkins
import socket
from xml.etree import ElementTree

STATUSES = {'blue': {'symbol': 'S',
                     'color': '\033[94m'},
            'red': {'symbol': 'F',
                    'color': '\033[91m'},
            'yellow': {'symbol': 'U',
                       'color': '\033[93m'},
            'disabled': {'symbol': 'D',
                         'color': '\033[97m'},
            'aborted': {'symbol': 'A',
                        'color': '\033[97m'}
            }

# 'green': '\033[92m',


ENDCOLLOR = '\033[0m'
ANIME_SYMBOL = ['..', '>>']


class CliException(Exception):
    pass


class JenkinsCli(object):
    SETTINGS_FILE_NAME = '.jenkins-cli'

    QUEUE_EMPTY_TEXT = "Building Queue is empty"

    INFO_TEMPLATE = ("Last build name: %s (result: %s)\n"
                     "Last success build name: %s\n"
                     "Build started: %s\n"
                     "Building now: %s\n"
                     "%s branch set to: %s")

    def __init__(self, args, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        self.jenkins = self.auth(args.host, args.username, args.password, timeout)

    @classmethod
    def auth(cls, host=None, username=None, password=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        if host is None or username is None or password is None:
            settings_dict = cls.read_settings_from_file()
            try:
                host = host or settings_dict['host']
                username = username or settings_dict.get('username', None)
                password = password or settings_dict.get('password', None)
            except KeyError:
                raise CliException('jenkins "host" has to be specified by the command-line options or in .jenkins-cli file')
        return jenkins.Jenkins(host, username, password, timeout)

    @classmethod
    def read_settings_from_file(cls):
        try:
            current_folder = os.getcwd()
            filename = os.path.join(current_folder, cls.SETTINGS_FILE_NAME)
            if not os.path.exists(filename):
                home_folder = os.path.expanduser("~")
                filename = os.path.join(home_folder, cls.SETTINGS_FILE_NAME)
                if not os.path.exists(filename):
                    return {}
            f = open(filename, 'r')
            jenkins_settings = f.read()
        except Exception as e:
            raise CliException('Error reading %s: %s' % (filename, e))

        settings_dict = {}
        for setting_line in jenkins_settings.split('\n'):
            if "=" in setting_line:
                key, value = setting_line.split("=", 1)
                settings_dict[key.strip()] = value.strip()
        return settings_dict

    def run_command(self, args):
        command = args.jenkins_command
        getattr(self, command)(args)

    def jobs(self, args):
        jobs = self._get_jobs(args)
        for job in jobs:
            color_status = job['color'].split('_')
            color = color_status[0]
            run_status = color_status[1] if len(color_status) == 2 else None
            status = STATUSES[color]
            print("%s%s%s%s %s" % (status['color'], status['symbol'], ANIME_SYMBOL[run_status == 'anime'], ENDCOLLOR, job['name']))

    def _get_jobs(self, args):
        jobs = self.jenkins.get_jobs()
        if args.a:
            jobs = [j for j in jobs if j.get('color') != 'disabled']
        # jobs = sorted(jobs, key=lambda j: j.get('name'))
        return jobs

    def queue(self, args):
        jobs = self.jenkins.get_queue_info()
        if jobs:
            for job in jobs:
                print("%s %s" % (job['task']['name'], job['why']))
        else:
            print(self.QUEUE_EMPTY_TEXT)

    def _check_job(self, job_name):
        job_name = self.jenkins.get_job_name(job_name)
        if not job_name:
            raise CliException('Job name does not esist')
        return job_name

    def _get_scm_name_and_node(self, xml_root):
        scm_name = 'UnknownSCM'
        branch_node = None
        try:
            scm = xml_root.find('scm')
            if scm.attrib['class'] == 'hudson.plugins.mercurial.MercurialSCM':
                scm_name = 'Mercurial'
                branch_node = scm.find('revision')
            elif scm.attrib['class'] == 'hudson.plugins.git.GitSCM':
                scm_name = 'Git'
                branch_node = scm.find('branches').find('hudson.plugins.git.BranchSpec').find('name')
        except AttributeError:
            pass
        return (scm_name, branch_node)

    def info(self, args):
        job_name = self._check_job(args.job_name)
        job_info = self.jenkins.get_job_info(job_name, 1)
        if not job_info:
            job_info = {}
        last_build = job_info.get('lastBuild', {})
        last_success_build = job_info.get('lastSuccessfulBuild', {})
        xml = self.jenkins.get_job_config(job_name)
        root = ElementTree.fromstring(xml.encode('utf-8'))
        scm_name, branch_node = self._get_scm_name_and_node(root)
        if branch_node is not None:
            branch_name = branch_node.text
        else:
            branch_name = 'Unknown branch'
        print(self.INFO_TEMPLATE % (last_build.get('fullDisplayName', 'Not Built'),
                                    last_build.get('result', 'Not Built'),
                                    last_success_build.get('fullDisplayName', 'Not Built'),
                                    datetime.datetime.fromtimestamp(last_build['timestamp'] / 1000) if last_build else 'Not Built',
                                    'Yes' if last_build.get('building') else 'No',
                                    scm_name,
                                    branch_name))

    def set_branch(self, args):
        job_name = self._check_job(args.job_name)
        xml = self.jenkins.get_job_config(job_name)
        root = ElementTree.fromstring(xml.encode('utf-8'))
        scm_name, branch_node = self._get_scm_name_and_node(root)
        if branch_node is not None:
            branch_node.text = args.branch_name
            new_xml = ElementTree.tostring(root)
            self.jenkins.reconfig_job(job_name, new_xml)
            print('Done')
        else:
            print("Can't set branch name")

    def start(self, args):
        for job in args.job_name:
            job_name = self._check_job(job)
            start_status = self.jenkins.build_job(job_name)
            print("%s: %s" % (job_name, 'started' if not start_status else start_status))

    def stop(self, args):
        job_name = self._check_job(args.job_name)
        info = self.jenkins.get_job_info(job_name)
        build_number = info['lastBuild'].get('number')
        if build_number and info['lastBuild'].get('building'):
            stop_status = self.jenkins.stop_build(job_name, build_number)
            print("%s: %s" % (job_name, 'stopped' if not stop_status else stop_status))
        else:
            print("%s job is not running" % job_name)

    def console(self, args):
        job_name = self._check_job(args.job_name)
        info = self.jenkins.get_job_info(job_name)
        print(info['lastBuild'])
        build_number = info['lastBuild'].get('number')
        console_out = self.jenkins.get_build_console_output(job_name, build_number)
        console_out = console_out.split('\n')
        last_line_num = len(console_out)
        if args.n:
            console_out = console_out[args.n:] if args.n < 0 else console_out[:args.n]
        print("\n".join(console_out))
        if args.i:
            build_info = self.jenkins.get_build_info(job_name, build_number)
            while build_info['building']:
                console_out = self.jenkins.get_build_console_output(job_name, build_number)
                console_out = console_out.split('\n')
                new_line_num = len(console_out)
                if new_line_num > last_line_num:
                    print("\n".join(console_out[last_line_num:]))
                    last_line_num = new_line_num
                time.sleep(3)
                build_info = self.jenkins.get_build_info(job_name, build_number)

    def building(self, args):
        args.a = True
        jobs = [j for j in self._get_jobs(args) if 'anime' in j['color']]
        if jobs:
            for job in jobs:
                info = self.jenkins.get_job_info(job['name'])
                build_number = info['lastBuild'].get('number')
                eta = "unknown"
                display_name = job['name']
                if build_number:
                    build_info = self.jenkins.get_build_info(job['name'], build_number)
                    eta = (build_info['timestamp'] + build_info['estimatedDuration']) / 1000 - time()
                    eta = datetime.timedelta(seconds=eta)
                    display_name = build_info['fullDisplayName']
                print("%s estimated time left %s" % (display_name, eta))
        else:
            print("Nothing is building now")
