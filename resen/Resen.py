#!/usr/bin/env python
####################################################################
#
#  Title: resen
#
#  Author: asreimer
#  Description: The resen tool for working with resen-core locally
#               which allows for listing available core docker
#               images, creating resen buckets, starting buckets,
#               curing (freezing) buckets, and uploading frozen
#
####################################################################


# TODO
# 1) list available resen-core version from dockerhub
# 2) create a bucket manifest from existing bucket
# 3) load a bucket from manifest file (supports moving from cloud to local, or from one computer to another)
# 4) keep track of whether a jupyter server is running or not already and provide shutdown_jupyter and open_jupyter commands
# 5) freeze a bucket
# 6) check for python 3, else throw error
# 7) when starting a bucket again, need to recreate the container if ports and/or storage locations changed. Can do so with: https://stackoverflow.com/a/33956387
#    until this happens, we cannot modify storage nor ports after a bucket has been started
# 8) check that a local port being added isn't already used by another bucket.
# 9) check that a local storage location being added isn't already used by another bucket.

    #     - add a location for home directory persistent storage
    #     - how many cpu/ram resources are allowed to be used?
    #     - json file contains all config info about buckets
    #         - used to share and freeze buckets
    #         - track information about buckets (1st time using, which are running?)

# The fuctions remove_storage and remove_port will probably be used MINMALLY.  Is it worth keeping them?


import os
import cmd         # for command line interface
import json        # used to store bucket manifests locally and for export
import time        # used for waiting (time.sleep())
import random      # used to generate tokens for jupyter server
import tempfile    # use this to get unique name for docker container
import webbrowser  # use this to open web browser
from pathlib import Path            # used to check whitelist paths
from subprocess import Popen, PIPE  # used for selinux detection
import tarfile
import shutil
import socket

from .DockerHelper import DockerHelper


class Resen():

    def __init__(self):

        self.resen_root_dir = self._get_config_dir()
        self.__locked = False
        self.__lock()

        self.dockerhelper = DockerHelper()
        # load
        self.load_config()
        self.valid_cores = self.__get_valid_cores()
        self.selinux = self.__detect_selinux()
        ### NOTE - Does this still need to include '/home/jovyan/work' for server compatability?
        ### If so, can we move the white list to resencmd.py? The server shouldn't every try to
        ### mount to an illegal location but the user might.
        self.storage_whitelist = ['/home/jovyan/mount']

    def load_config(self):
        '''
        Load config file that contains information on existing buckets.
        '''
        # define config file name
        bucket_config = os.path.join(self.resen_root_dir,'buckets.json')

        try:
            with open(bucket_config,'r') as f:
                params = json.load(f)
        # if config file doesn't exist, initialize and empty list
        except FileNotFoundError:
            params = list()

        # # check if buckets.json exists, if not, initialize empty dictionary
        # if not os.path.exists(bucket_config):
        #     params = list()
        #
        # else:
        # # if it does exist, load it and return
        # # TODO: handle exceptions due to file reading problems (incorrect file permissions)
        #     with open(bucket_config,'r') as f:
        #         params = json.load(f)

        self.buckets = params
        self.bucket_names = [x['bucket']['name'] for x in self.buckets]

        # TODO: update status of buckets to double check that status is the same as in bucket.json

    def save_config(self):
        '''
        Save config file with information on existing buckets
        '''
        # define config file name
        bucket_config = os.path.join(self.resen_root_dir,'buckets.json')
        # TODO: handle exceptions due to file writing problems (no free disk space, incorrect file permissions)
        with open(bucket_config,'w') as f:
            json.dump(self.buckets,f)


    def get_bucket(self,bucket_name):
        '''
        Retrieve a bucket object by its name.  Raise an error if the bucket does not exist.
        '''
        try:
            ind = self.bucket_names.index(bucket_name)
        except ValueError:
            raise ValueError('Bucket with name: %s does not exist!' % bucket_name)

        bucket = self.buckets[ind]
        return bucket


    def create_bucket(self,bucket_name):
        '''
        Create "empty" bucket.  Only name assigned.
        '''
        # raise error if bucket_name already in uses
        if bucket_name in self.bucket_names:
            raise ValueError("Bucket with name: %s already exists!" % (bucket_name))

        params = dict()
        params['bucket'] = dict()
        params['docker'] = dict()
        params['bucket']['name'] = bucket_name
        params['docker']['image'] = None
        params['docker']['container'] = None
        params['docker']['port'] = list()
        params['docker']['storage'] = list()
        params['docker']['status'] = None
        params['docker']['jupyter'] = dict()
        params['docker']['jupyter']['token'] = None
        params['docker']['jupyter']['port'] = None

        # now add the new bucket to the self.buckets config and then update the config file
        self.buckets.append(params)
        self.bucket_names = [x['bucket']['name'] for x in self.buckets]
        self.save_config()

        return


    def remove_bucket(self,bucket_name):
        '''
        Remove a bucket, including the corresponding container.
        '''

        self.update_bucket_statuses() # Is this nessesary?
        bucket = self.get_bucket(bucket_name)

        # cannot remove bucket if currently running - raise error
        if bucket['docker']['status'] == 'running':
            raise RuntimeError('ERROR: Bucket %s is running, cannot remove.' % (bucket['bucket']['name']))

        # if docker container created, remove it first and update status
        if bucket['docker']['status'] in ['created','exited'] and bucket['docker']['container'] is not None:
            self.dockerhelper.remove_container(bucket)
            bucket['docker']['status'] = None
            bucket['docker']['container'] = None
            self.save_config()

        # identify bucket index and remove it from both buckets and bucket_names
        ind = self.bucket_names.index(bucket_name)
        self.buckets.pop(ind)
        self.bucket_names.pop(ind)
        self.save_config()

        return


    def set_image(self,bucket_name,docker_image):
        '''
        Set the image to use in a bucket
        '''
        # It should be fine to overwrite an existing image if the container hasn't been started yet
        # would be helpful to save image org and repo as well for export purposes
        # should we check if the image ID is available locally and if not pull it HERE insead of in the container creation?

        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if container has been created, cannot change the image
        if bucket['docker']['status'] is not None:
            raise RuntimeError("Bucket has already been started, cannot remove port: %s" % (local))

        # check that input is a valid image
        valid_versions = [x['version'] for x in self.valid_cores]
        if not docker_image in valid_versions:
            raise ValueError("Invalid resen-core version %s. Valid version: %s" % (docker_image,', '.join(valid_versions)))

        ind = valid_versions.index(docker_image)
        image = self.valid_cores[ind]
        bucket['docker']['image'] = image['version']
        bucket['docker']['image_id'] = image['image_id']
        bucket['docker']['pull_image'] = '%s/%s@%s' % (image['org'],image['repo'],image['repodigest'])

        self.save_config()

        return


    def add_storage(self,bucket_name,local,container,permissions='r'):
        '''
        Add a host machine storage location to the bucket.
        '''
        # Should this be called 'storage' or 'mount'?  Docker calls these mounts, but here, either will do.
        # TODO: investiage difference between mounting a directory and fileblock
        #       See: https://docs.docker.com/storage/

        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if container has been created, cannot add storage
        if bucket['docker']['status'] is not None:
            raise RuntimeError("Bucket has already been started, cannot add storage: %s" % (local))

        # check if input locations already exist in bucket list of storage
        existing_local = [x[0] for x in bucket['docker']['storage']]
        if local in existing_local:
            raise FileExistsError('Local storage location already in use in bucket!')
        existing_container = [x[1] for x in bucket['docker']['storage']]
        if container in existing_container:
            raise FileExistsError('Container storage location already in use in bucket!')

        # check that local file path exists
        if not Path(local).is_dir():
            raise FileNotFoundError('Cannot find local storage location!')

        # check that user is mounting in a whitelisted location
        valid = False
        child = Path(container)
        for loc in self.storage_whitelist:
            p = Path(loc)
            if p == child or p in child.parents:
                valid = True
        if not valid:
            raise ValueError("Invalid mount location. Can only mount storage into: %s." % ', '.join(self.storage_whitelist))

        # check and adjust permissions
        if not permissions in ['r','ro','rw']:
            raise ValueError("Invalid permissions. Valid options are 'r' and 'rw'.")

        if permissions in ['r','ro']:
            permissions = 'ro'

        if self.selinux:
            permissions += ',Z'

        # Add storage location
        bucket['docker']['storage'].append([local,container,permissions])
        self.save_config()

        return


    def remove_storage(self,bucket_name,local):
        '''
        Remove a storage location from the bucket.
        '''

        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if container created, cannot remove storage
        if bucket['docker']['status'] is not None:
            raise RuntimeError("Bucket has already been started, cannot add storage: %s" % (local))

        # find index of storage
        existing_storage = [x[0] for x in bucket['docker']['storage']]
        try:
            ind = existing_storage.index(local)
        # raise exception if input location does not exist
        except ValueError:
            raise FileNotFoundError("Storage location %s not associated with bucket %s" % (local,bucket_name))

        bucket['docker']['storage'].pop(ind)
        self.save_config()

        return


    def add_port(self,bucket_name,local=None,container=None,tcp=True):
        '''
        Add a port to the bucket
        '''
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if container has been created, cannot add port
        if bucket['docker']['status'] is not None:
            raise RuntimeError("Bucket has already been started, cannot add port: %s" % (local))

        if not local and not container:
            # this is not atomic, so it is possible that another process might snatch up the port
            local = self.get_port()
            container = local

        else:
            # check if local/container port already exists in list of ports
            existing_local = [x[0] for x in bucket['docker']['port']]
            if local in existing_local:
                raise ValueError('Local port location already in use in bucket!')
            existing_container = [x[1] for x in bucket['docker']['port']]
            if container in existing_container:
                raise ValueError('Container port location already in use in bucket!')

            # TODO: check if port location exists on host - maybe not?  If usuer manually assigns port, ok to trust they know what they're doing?
            # check if port avaiable on host (from https://stackoverflow.com/questions/2470971/fast-way-to-test-if-a-port-is-in-use-using-python)
            # DOESN'T WORK - LOOK INTO THIS MORE LATER
            # with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            #     print(s.connect_ex(('localhost', local)))
            #     if s.connect_ex(('localhost', local)):
            #         raise RuntimeError("Port %s in use and cannot be assigned to bucket" % local)

        bucket['docker']['port'].append([local,container,tcp])
        self.save_config()

        return


    def remove_port(self,bucket_name,local):
        '''
        Remove a port from the bucket
        '''
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if container has been created, cannot remove port
        if bucket['docker']['status'] is not None:
            raise RuntimeError("Bucket has already been started, cannot remove port: %s" % (local))

        # find port and remove it
        existing_port = [x[0] for x in bucket['docker']['port']]
        try:
            ind = existing_port.index(local)
        # raise exception if port is not assigned to bucket
        except ValueError:
            raise ValueError("Port location %s not associated with bucket %s" % (local,bucket_name))

        bucket['docker']['port'].pop(ind)
        self.save_config()

        return

    def get_port(self):
        # this is not atomic, so it is possible that another process might snatch up the port
        port = 9000
        assigned_ports = [y[0] for x in self.buckets for y in x['docker']['port']]

        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                assigned = s.connect_ex(('localhost', port)) == 0
            if not assigned and not port in assigned_ports:
                return port
            else:
                port += 1


    def start_bucket(self,bucket_name):
        '''
        Start the bucket
        '''
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if bucket is already running, do nothing
        if bucket['docker']['status'] in ['running']:
            # print('Bucket %s is already running!' % (bucket['bucket']['name']))
            return

        # Make sure we have an image assigned to the bucket
        if bucket['docker']['image_id'] is None:
            raise RuntimeError('Bucket does not have an image assigned to it.')

        # If a container hasn't been created yet, create one
        if bucket['docker']['container'] is None:
            container_id, status = self.dockerhelper.create_container(bucket)
            bucket['docker']['container'] = container_id
            bucket['docker']['status'] = status
            self.save_config()

        self.update_bucket_statuses() # Nessisary?  I believe this is taken care of above
        # Is this second call nessisary?
        bucket = self.get_bucket(bucket_name)
        # start the container and update status
        status = self.dockerhelper.start_container(bucket)
        bucket['docker']['status'] = status
        self.save_config()

        # raise error if bucket did not start sucessfully
        if status != 'running':
            raise RuntimeError('Failed to start bucket %s' % (bucket['bucket']['name']))

        return True


    def stop_bucket(self,bucket_name):
        '''
        Stop bucket
        '''

        self.update_bucket_statuses() # Nessisary?
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if bucket is already stopped, do nothing
        if bucket['docker']['status'] in ['created', 'exited']:
            # print('Bucket %s is not running!' % (bucket['bucket']['name']))
            return

        # stop the container and update status
        status = self.dockerhelper.stop_container(bucket)
        bucket['docker']['status'] = status
        self.save_config()

        if status != 'exited':
            raise RuntimeError('Failed to stop bucket %s' % (bucket['bucket']['name']))

        return


    def execute_command(self,bucket_name,command,detach=True):
        '''
        Execute a command in the bucket.  Returns the exit code and output form the command, if applicable (if not detached?).
        '''
        self.update_bucket_statuses() # Nessesary?
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # raise error if bucket not running
        if bucket['docker']['status'] not in ['running']:
            raise RuntimeError('Bucket %s is not running!' % (bucket['bucket']['name']))

        # execute command
        result = self.dockerhelper.execute_command(bucket,command,detach=detach)
        code, output = result
        if (detach and code is not None) or (not detach and code!=0):
            raise RuntimeError('Failed to execute command %s' % (command))

        return result

    def set_sudo(self, bucket_name, password='ganimede'):
        # get bucket
        bucket = self.get_bucket(bucket_name)

        command = 'echo "jovyan:%s | chpasswd" && adduser jovyan sudo' % password
        ## NEED SOMETHING ELSE!!!
        # This sets a password and adds jovyan to the sudo group, but /etc/sudoers still has
        # %sudo ALL=(ALL:ALL) ALL
        # commented out, so sudo group can't act as root

        self.execute_command(bucket_name, command)

        return

    def start_jupyter(self,bucket_name,local_port=None,container_port=None):
        '''
        Start a jupyter server in the bucket and open a web browser window to a jupyter lab session.  Server will
        use the specified local and container ports (ports must be a matched pair!)
        '''
        # TODO:
        # Identify port ONLY with local port?
        # Select port automatically if none provided?
        # Allow multiple jupyter servers to run simultaniously?  Would this ever be useful?


        # get bucket
        bucket = self.get_bucket(bucket_name)

        # check if jupyter server already running - if so, proint the url to the screen
        pid = self.get_jupyter_pid(bucket_name)
        if not pid is None:
            port = bucket['docker']['jupyter']['port']
            token = bucket['docker']['jupyter']['token']
            url = 'http://localhost:%s/?token=%s' % (port,token)
            print("Jupyter lab is already running and can be accessed in a browser at: %s" % (url))
            return

        # if ports are not specified, use the first port set from the bucket
        if not local_port and not container_port:
            local_port = bucket['docker']['port'][0][0]
            container_port = bucket['docker']['port'][0][1]

        # set a random token and form
        token = '%048x' % random.randrange(16**48)
        command = "bash -cl 'source /home/jovyan/envs/py36/bin/activate py36 && jupyter lab --no-browser --ip 0.0.0.0 --port %s --NotebookApp.token=%s --KernelSpecManager.ensure_native_kernel=False'"
        command = command % (container_port, token)

        # exectute command to start jupyter server
        self.execute_command(bucket_name,command,detach=True)
        time.sleep(0.1)

        # now check that jupyter is running
        # Will this create a race condition?
        self.update_bucket_statuses() # nessesary?
        pid = self.get_jupyter_pid(bucket_name)

        if pid is None:
            raise RuntimeError("Failed to start jupyter server!")

        # set jupyter token an port
        bucket['docker']['jupyter']['token'] = token
        bucket['docker']['jupyter']['port'] = local_port
        self.save_config()

        # print url to access jupyter lab to screen and automatically open in web browser
        url = 'http://localhost:%s/?token=%s' % (local_port,token)
        print("Jupyter lab can be accessed in a browser at: %s" % (url))
        time.sleep(3)
        webbrowser.open(url)

        return

    def stop_jupyter(self,bucket_name):
        '''
        Stop jupyter server
        '''
        # get bucket
        bucket = self.get_bucket(bucket_name)

        # if jupyter server not running, do nothing
        pid = self.get_jupyter_pid(bucket_name)
        if pid is None:
            return True

        # form python command to stop jupyter and execute it
        port = bucket['docker']['jupyter']['port']
        python_cmd = 'from notebook.notebookapp import shutdown_server, list_running_servers; '
        python_cmd += 'svrs = [x for x in list_running_servers() if x[\\\"port\\\"] == %s]; ' % (port)
        python_cmd += 'sts = True if len(svrs) == 0 else shutdown_server(svrs[0]); print(sts)'
        command = "bash -cl '/home/jovyan/envs/py36/bin/python -c \"%s \"'" % (python_cmd)
        status = self.execute_command(bucket_name,command,detach=False)

        # self.update_bucket_statuses() # Nessisary?

        # now verify it is dead
        pid = self.get_jupyter_pid(bucket_name)
        if not pid is None:
            raise RuntimeError("Failed to stop jupyter lab.")

        # Update jupyter token and port to None
        bucket['docker']['jupyter']['token'] = None
        bucket['docker']['jupyter']['port'] = None
        self.save_config()

        return


    def get_jupyter_pid(self,bucket_name):
        '''
        Get PID for the jupyter server running in a particular bucket
        '''
        code, output = self.execute_command(bucket_name, 'ps -ef', detach=False)
        output = output.decode('utf-8').split('\n')

        pid = None
        for line in output:
            if ('jupyter-lab' in line or 'jupyter lab' in line) and '--no-browser --ip 0.0.0.0' in line:
                parsed_line = [x for x in line.split(' ') if x != '']
                pid = parsed_line[1]
                break

        return pid


    def export_bucket(self, bucket_name, outfile, exclude_mounts=[]):
        '''
        Export a bucket
        '''
        # Where should all this temporary file creation occur? Where should bucket_dir be?
        # tar compression - what should we use?
        # some kind of status bar would be useful - this takes a while
        # Should we include "human readable" metadata?
        # let users select specific mounts to include
        # check size of mounts
        # check harddrive space for commit

        # get bucket
        bucket = self.get_bucket(bucket_name)

        # create temporary directory that will become the final bucket tar file
        bucket_dir = Path(os.getcwd()).joinpath('resen_{}'.format(bucket_name))
        os.mkdir(bucket_dir)

        # initialize manifest
        manifest = dict()

        # export container to image *.tar file
        image_file_name = '{}_image.tar'.format(bucket_name)
        status = self.dockerhelper.export_container(bucket, tag='export', filename=bucket_dir.joinpath(image_file_name))
        manifest['image'] = image_file_name

        # save all mounts individually as *.tgz files
        manifest['mounts'] = list()
        for mount in bucket['docker']['storage']:
            # skip mount if it is listed in exclude_mounts
            if mount[0] in exclude_mounts:
                continue

            source_dir = Path(mount[0])
            mount_file_name = '{}_mount.tgz'.format(source_dir.name)
            with tarfile.open(bucket_dir.joinpath(mount_file_name), "w:gz") as tar:
                tar.add(source_dir, arcname=source_dir.name)

            manifest['mounts'].append([mount_file_name, mount[1], mount[2]])

        # save manifest file
        with open(bucket_dir.joinpath('manifest.json'),'w') as f:
            json.dump(manifest, f)

        # save entire bucket as tgz file
        with tarfile.open(outfile, 'w:gz') as tar:
            tar.add(bucket_dir, arcname=bucket_dir.name)

        # remove temporary directory
        shutil.rmtree(bucket_dir)

        return

    def import_bucket(self,bucket_name,filename):
        '''
        Import bucket from tgz file.  Extract image and mounts.  Set up new bucket with image and mounts.
        This deos NOT add ports (these should be selected based on new local computer) and container is NOT created/started.
        '''
        # Should original tar files be removed after they're extracted?
        # Where should the bucket mounts be extracted to?

        # untar bucket file
        with tarfile.open(filename) as tar:
            tar.extractall()
            bucket_dir = Path(filename).parent.joinpath(tar.getnames()[0])

        # read manifest
        with open(bucket_dir.joinpath('manifest.json'),'r') as f:
            manifest = json.load(f)

        # create new bucket
        self.create_bucket(bucket_name)
        bucket = self.get_bucket(bucket_name)

        # load image
        image_id = self.dockerhelper.import_image(bucket_dir.joinpath(manifest['image']))
        # add image to bucket
        # Don't really need the image or pull image fields, but there's errors if they're not set?
        bucket['docker']['image'] = ''
        bucket['docker']['image_id'] = image_id
        bucket['docker']['pull_image'] = ''

        # add mounts to bucket
        for mount in manifest['mounts']:
            # extract mount from tar file
            with tarfile.open(bucket_dir.joinpath(mount[0])) as tar:
                tar.extractall(path=bucket_dir)
                local = bucket_dir.joinpath(tar.getnames()[0])
            # add mount to bucket with original container path
            self.add_storage(bucket_name,local.as_posix(),mount[1],permissions=mount[2])

        return


    def list_buckets(self,names_only=False,bucket_name=None):
        '''
        Generate a nicely formated string listing all the buckets and their statuses
        '''
        # TODO - remove name_only option?  Can access this with bucket_names
        # Add full status report for a single bucket
        if bucket_name is None:
            if names_only:
                print("{:<0}".format("Bucket Name"))
                for name in self.bucket_names:
                    print("{:<0}".format(str(name)))
            else:

                print("{:<20}{:<25}{:<25}".format("Bucket Name","Docker Image","Status"))
                for bucket in self.buckets:
                    name = self.__trim(str(bucket['bucket']['name']),18)
                    image = self.__trim(str(bucket['docker']['image']),23)
                    status = self.__trim(str(bucket['docker']['status']),23)
                    print("{:<20}{:<25}{:<25}".format(name, image, status))

        else:   # TODO, print all bucket info for bucket_name
            bucket = self.get_bucket(bucket_name)
            print(bucket)

        return

    # def remove_bucket(self,bucket_name):
    #
    #     self.update_bucket_statuses()
    #     bucket = self.get_bucket(bucket_name)
    #
    #     # cannot remove bucket if currently running
    #     if bucket['docker']['status'] == 'running':
    #         raise RuntimeError('ERROR: Bucket %s is running, cannot remove.' % (bucket['bucket']['name']))
    #
    #     # if docker container created, remove it first
    #     if bucket['docker']['status'] in ['created','exited'] and bucket['docker']['container'] is not None:
    #         # then we can remove container and update status
    #         # success = self.dockerhelper.remove_container(bucket['docker']['container'])
    #         success = self.dockerhelper.remove_container(bucket)
    #         bucket['docker']['status'] = None
    #         bucket['docker']['container'] = None
    #         self.save_config()
    #
    #     ind = self.bucket_names.index(bucket_name)
    #     # bucket = self.buckets[ind]
    #     # if bucket['docker']['container'] is None:
    #     self.buckets.pop(ind)
    #     self.bucket_names = [x['bucket']['name'] for x in self.buckets]
    #     self.save_config()
    #     return True
    #     # else:
    #     #     print('ERROR: Failed to remove bucket %s' % (bucket['bucket']['name']))
    #     #     return False

    # def load(self):
    # # - import a bucket
    # #     - docker container export? (https://docs.docker.com/engine/reference/commandline/container_export/)
    # #     - check iodide, how do they share
    #     pass
    #
    # def export(self):
    # # export a bucket
    # #
    #     pass
    #
    # def freeze_bucket(self):
    # # - bucket freeze (create docker image)
    # #     - make a Dockerfile, build it, save it to tar.gz
    # #     - docker save (saves an image): https://docs.docker.com/engine/reference/commandline/save/
    # #       or docker container commit: https://docs.docker.com/engine/reference/commandline/container_commit/
    # #     - docker image load (opposite of docker save): https://docs.docker.com/engine/reference/commandline/image_load/
    #     pass


    def update_bucket_statuses(self):
        '''
        Update container status for all buckets
        '''
        # Is this nessesary?  save_config() in particular seems to be called multiple times for each update (once here and once in calling function.)
        for bucket in self.buckets:

            if bucket['docker']['container'] is None:
                continue

            status = self.dockerhelper.get_container_status(bucket)
            bucket['docker']['status'] = status
            self.save_config()

    # def get_container(self,bucket_name):
    #     if not bucket_name in self.bucket_names:
    #         print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
    #         return False
    #
    #     ind = self.bucket_names.index(bucket_name)
    #     return self.buckets[ind]['docker']['container']

    def __get_valid_cores(self):
        # TODO: download json file from resen-core github repo
        #       and if that fails, fallback to hardcoded list
        # LJL:2019-10-07: Hardcoded in new resen-lite image for testing purposes - not available on docker hub yet
        return [{"version":"2019.1.0rc2","repo":"resen-core","org":"earthcubeingeo",
                 "image_id":'sha256:8b4750aa5186bdcf69a50fa10b0fd24a7c2293ef6135a9fdc594e0362443c99c',
                 "repodigest":'sha256:2fe3436297c23a0d5393c8dae8661c40fc73140e602bd196af3be87a5e215bc2'},
                 {"version":"latest","repo":"resen-lite","org":"earthcubeingeo",
                  "image_id":'sha256:134707a783d88358bcf4a850cbf602134a26dd6381a41b3af9960b8298f8caf6',
                  "repodigest":''},]

    def _get_config_dir(self):
        appname = 'resen'

        if 'APPDATA' in os.environ:
            confighome = os.environ['APPDATA']
        elif 'XDG_CONFIG_HOME' in os.environ:
            confighome = os.environ['XDG_CONFIG_HOME']
        else:
            confighome = os.path.join(os.environ['HOME'],'.config')
        configpath = os.path.join(confighome, appname)

        # TODO: add error checking
        if not os.path.exists(configpath):
            os.makedirs(configpath)

        return configpath

    def __lock(self):
        self.__lockfile = os.path.join(self.resen_root_dir,'lock')
        if os.path.exists(self.__lockfile):
            raise RuntimeError('Another instance of Resen is already running!')

        with open(self.__lockfile,'w') as f:
            f.write('locked')
        self.__locked = True

    def __unlock(self):
        if not self.__locked:
            return

        try:
            os.remove(self.__lockfile)
            self.__locked = False
        except FileNotFoundError:
            pass
        except Exception as e:
            print("WARNING: Unable to remove lockfile: %s" % str(e))

    def __detect_selinux(self):
        try:
            p = Popen(['/usr/sbin/getenforce'], stdin=PIPE, stdout=PIPE, stderr=PIPE)
            output, err = p.communicate()
            output = output.decode('utf-8').strip('\n')
            rc = p.returncode

            if rc == 0 and output == 'Enforcing':
                return True
            else:
                return False
        except FileNotFoundError:
            return False

    def __trim(self,string,length):
        if len(string) > length:
            return string[:length-3]+'...'
        else:
            return string


    def __del__(self):
        self.__unlock()


    # TODO: def reset_bucket(self,bucket_name):
    # used to reset a bucket to initial state (stop existing container, delete it, create new container)




#     def list_cores():
#         # list available docker images
#         # - list/pull docker image from docker hub
# #     - docker pull: https://docs.docker.com/engine/reference/commandline/pull/#pull-an-image-from-docker-hub
#         pass



# Configuration information:
#    - store it in .json file somewhere
#    - read the .json file and store config in config classes


# handle all of the bucket configuration info including reading
# and writing bucket config

def main():

    pass


if __name__ == '__main__':

    main()
