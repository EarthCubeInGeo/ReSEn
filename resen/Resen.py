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


import os
import cmd         # for command line interface
import json        # used to store bucket manifests locally and for export
import time        # used for waiting (time.sleep())
import random      # used to generate tokens for jupyter server
import tempfile    # use this to get unique name for docker container
import webbrowser  # use this to open web browser
from pathlib import Path            # used to check whitelist paths
from subprocess import Popen, PIPE  # used for selinux detection

from .DockerHelper import DockerHelper


class Resen():
    def __init__(self):
        self.base_config_dir = self._get_config_dir()
        self.__locked = False
        self.__lock()

        self.bucket_manager = BucketManager(self.base_config_dir)

    def create_bucket(self,bucket_name):
        return self.bucket_manager.create_bucket(bucket_name)

    def list_buckets(self,names_only=False,bucket_name=None):
        return self.bucket_manager.list_buckets(names_only=names_only,bucket_name=bucket_name)

    def remove_bucket(self,bucket_name):
        return self.bucket_manager.remove_bucket(bucket_name)

    def add_storage(self,bucket_name,local,container,permissions):
        return self.bucket_manager.add_storage(bucket_name,local,container,permissions)

    def remove_storage(self,bucket_name,local):
        return self.bucket_manager.remove_storage(bucket_name,local)

    def add_port(self,bucket_name,local,container,tcp=True):
        return self.bucket_manager.add_port(bucket_name,local,container,tcp=tcp)

    def remove_port(self,bucket_name,local):
        return self.bucket_manager.remove_port(bucket_name,local)

    def add_image(self,bucket_name,docker_image):
        return self.bucket_manager.add_image(bucket_name,docker_image)

    def start_bucket(self,bucket_name):
        return self.bucket_manager.start_bucket(bucket_name)

    def stop_bucket(self,bucket_name):
        return self.bucket_manager.stop_bucket(bucket_name)

    def start_jupyter(self,bucket_name,local,container):
        return self.bucket_manager.start_jupyter(bucket_name,local,container)

    def stop_jupyter(self,bucket_name):
        return self.bucket_manager.stop_jupyter(bucket_name)

    def export_bucket(self,bucket_name,outfile):
        return self.bucket_manager.export_bucket(bucket_name,outfile)

    def import_bucket(self, filename):
        return self.bucket_manager.import_bucket(filename)

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
        self.__lockfile = os.path.join(self.base_config_dir,'lock')
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

    def __del__(self):
        self.__unlock()


# All the bucket stuff
# TODO: check status of bucket before updating it in case the bucket has changed status since last operation
class BucketManager():
# - use a bucket
#     - how many are allowed to run simultaneously?
#     - use the bucket how? only through jupyter notebook/lab is Ashton's vote. Terminal provided there

    def __init__(self,resen_root_dir):

        self.resen_root_dir = resen_root_dir
        self.dockerhelper = DockerHelper()
        # load
        self.load_config()
        self.valid_cores = self.__get_valid_cores()
        self.selinux = self.__detect_selinux()
        ### NOTE - Does this still need to include '/home/jovyan/work' for server compatability?
        ### If so, can we move the white list to resencmd.py? The server shouldn't every try to
        ### mount to an illegal location but the user might.
        self.storage_whitelist = ['/home/jovyan/mount']

    def __get_valid_cores(self):
        # TODO: download json file from resen-core github repo
        #       and if that fails, fallback to hardcoded list
        return [{"version":"2019.1.0rc2","repo":"resen-core","org":"earthcubeingeo",
                 "image_id":'sha256:8b4750aa5186bdcf69a50fa10b0fd24a7c2293ef6135a9fdc594e0362443c99c',
                 "repodigest":'sha256:2fe3436297c23a0d5393c8dae8661c40fc73140e602bd196af3be87a5e215bc2'},]

    def load_config(self):
        bucket_config = os.path.join(self.resen_root_dir,'buckets.json')
        # check if buckets.json exists, if not, initialize empty dictionary
        if not os.path.exists(bucket_config):
            params = list()

        else:
        # if it does exist, load it and return
        # TODO: handle exceptions due to file reading problems (incorrect file permissions)
            with open(bucket_config,'r') as f:
                params = json.load(f)

        self.buckets = params
        self.bucket_names = [x['bucket']['name'] for x in self.buckets]

        # TODO: update status of buckets to double check that status is the same as in bucket.json

    def save_config(self):
        bucket_config = os.path.join(self.resen_root_dir,'buckets.json')
        # TODO: handle exceptions due to file writing problems (no free disk space, incorrect file permissions)
        with open(bucket_config,'w') as f:
            json.dump(self.buckets,f)

    def create_bucket(self,bucket_name):
    #     - add a location for home directory persistent storage
    #     - how many cpu/ram resources are allowed to be used?
    #     - json file contains all config info about buckets
    #         - used to share and freeze buckets
    #         - track information about buckets (1st time using, which are running?)
        if bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s already exists!" % (bucket_name))
            return False

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

        return True

    def list_buckets(self,names_only=False,bucket_name=None):
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
            if not bucket_name in self.bucket_names:
                print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
                return False

            ind = self.bucket_names.index(bucket_name)
            # TODO: make this print a nice table
            print(self.buckets[ind])

        return True

    def __trim(self,string,length):
        if len(string) > length:
            return string[:length-3]+'...'
        else:
            return string

    def remove_bucket(self,bucket_name):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        self.update_bucket_statuses()
        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]

        if bucket['docker']['status'] == 'running':
            #container is running and we should throw an error
            print('ERROR: Bucket %s is running, cannot remove.' % (bucket['bucket']['name']))
            return False

        if bucket['docker']['status'] in ['created','exited'] and bucket['docker']['container'] is not None:
            # then we can remove container and update status
            success = self.dockerhelper.remove_container(bucket['docker']['container'])
            if success:
                self.buckets[ind]['docker']['status'] = None
                self.buckets[ind]['docker']['container'] = None
                self.save_config()

        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]
        if bucket['docker']['container'] is None:
            self.buckets.pop(ind)
            self.bucket_names = [x['bucket']['name'] for x in self.buckets]
            self.save_config()
            return True
        else:
            print('ERROR: Failed to remove bucket %s' % (bucket['bucket']['name']))
            return False

    def load(self):
    # - import a bucket
    #     - docker container export? (https://docs.docker.com/engine/reference/commandline/container_export/)
    #     - check iodide, how do they share
        pass

    def export(self):
    # export a bucket
    #
        pass

    def freeze_bucket(self):
    # - bucket freeze (create docker image)
    #     - make a Dockerfile, build it, save it to tar.gz
    #     - docker save (saves an image): https://docs.docker.com/engine/reference/commandline/save/
    #       or docker container commit: https://docs.docker.com/engine/reference/commandline/container_commit/
    #     - docker image load (opposite of docker save): https://docs.docker.com/engine/reference/commandline/image_load/
        pass

    def add_storage(self,bucket_name,local,container,permissions='r'):
        # TODO: investiage difference between mounting a directory and fileblock
        #       See: https://docs.docker.com/storage/

        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        # check if bucket is running
        if self.buckets[ind]['docker']['status'] is not None:
            print("ERROR: Bucket has already been started, cannot add storage: %s" % (local))
            return False

        # check if storage already exists in list of storage
        existing_local = [x[0] for x in self.buckets[ind]['docker']['storage']]
        if local in existing_local:
            print("ERROR: Local storage location already in use in bucket!")
            return False
        existing_container = [x[1] for x in self.buckets[ind]['docker']['storage']]
        if container in existing_container:
            print("ERROR: Container storage location already in use in bucket!")
            return False

        # check that user is mounting in a whitelisted location
        # this is local use specific - move to resencmd?
        valid = False
        child = Path(container)
        for loc in self.storage_whitelist:
            p = Path(loc)
            if p == child or p in child.parents:
                valid = True
        if not valid:
            print("ERROR: Invalid mount location. Can only mount storage into: %s." % ', '.join(self.storage_whitelist))
            return False

        if not permissions in ['r','ro','rw']:
            print("ERROR: Invalid permissions. Valid options are 'r' and 'rw'.")
            return False

        if permissions in ['r','ro']:
            permissions = 'ro'

        if self.selinux:
            permissions += ',Z'

        # TODO: check if storage location exists on host
        self.buckets[ind]['docker']['storage'].append([local,container,permissions])
        self.save_config()

        return True

    # Function obsolete?
    def remove_storage(self,bucket_name,local):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        # check if bucket is running
        if self.buckets[ind]['docker']['status'] is not None:
            print("ERROR: Bucket has already been started, cannot remove storage: %s" % (local))
            return False

        # check if storage already exists in list of storage
        existing_storage = [x[0] for x in self.buckets[ind]['docker']['storage']]
        try:
            ind2 = existing_storage.index(local)
            self.buckets[ind]['docker']['storage'].pop(ind2)
            self.save_config()
        except ValueError:
            print("ERROR: Storage location %s not associated with bucket %s" % (local,bucket_name))
            return False

        return True

    def add_port(self,bucket_name,local,container,tcp=True):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        # check if bucket is running
        if self.buckets[ind]['docker']['status'] is not None:
            print("ERROR: Bucket has already been started, cannot add port: %s" % (local))
            return False

        # check if local port already exists in list of ports
        existing_local = [x[0] for x in self.buckets[ind]['docker']['port']]
        if local in existing_local:
            print("ERROR: Local port location already in use in bucket!")
            return False

        # TODO: check if port location exists on host
        self.buckets[ind]['docker']['port'].append([local,container,tcp])
        self.save_config()

        return True

    # function obsolete?
    def remove_port(self,bucket_name,local):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        # check if bucket is running
        if self.buckets[ind]['docker']['status'] is not None:
            print("ERROR: Bucket has already been started, cannot remove port: %s" % (local))
            return False

        # check if port already exists in list of port
        existing_port = [x[0] for x in self.buckets[ind]['docker']['port']]
        try:
            ind2 = existing_port.index(local)
            self.buckets[ind]['docker']['port'].pop(ind2)
            self.save_config()
        except ValueError:
            print("ERROR: port location %s not associated with bucket %s" % (local,bucket_name))
            return False

        return True

    def add_image(self,bucket_name,docker_image):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        # TODO: check if "docker_image" is a valid resen-core image

        # check if image is already added  exists in list of storage
        ind = self.bucket_names.index(bucket_name)
        existing_image = self.buckets[ind]['docker']['image']
        if not existing_image is None:
            print("ERROR: Image %s was already added to bucket %s" % (existing_image,bucket_name))
            return False

        valid_versions = [x['version'] for x in self.valid_cores]
        if not docker_image in valid_versions:
            print("ERROR: Invalid resen-core version %s. Valid version: %s" % (docker_image,', '.join(valid_versions)))
            return False

        for x in self.valid_cores:
            if docker_image == x['version']:
                image = x['version']
                image_id = x['image_id']
                pull_image = '%s/%s@%s' % (x['org'],x['repo'],x['repodigest'])
                break

        self.buckets[ind]['docker']['image'] = image
        self.buckets[ind]['docker']['image_id'] = image_id
        self.buckets[ind]['docker']['pull_image'] = pull_image
        self.save_config()

        return True

    # TODO: def change_image(self,bucket_name,new_docker_image)
    # but only if container=None and status=None, in other words, only if the bucket has never been started.


    def start_bucket(self,bucket_name):
        # check if container has been previously started, create one if needed, start bucket if not running
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]

        # Make sure we have an image assigned to the bucket
        existing_image = bucket['docker']['image']
        if existing_image is None:
            print("ERROR: Bucket does not have an image assigned to it.")
            return False

        if bucket['docker']['container'] is None:
            # no container yet created, so create one
            kwargs = dict()
            kwargs['ports'] = bucket['docker']['port']
            kwargs['storage'] = bucket['docker']['storage']
            kwargs['bucket_name'] = bucket['bucket']['name']
            kwargs['image_name'] = bucket['docker']['image']
            kwargs['image_id'] = bucket['docker']['image_id']
            kwargs['pull_image'] = bucket['docker']['pull_image']
            container_id = self.dockerhelper.create_container(**kwargs)

            if container_id is None:
                print("ERROR: Failed to create container")
                return False

            self.buckets[ind]['docker']['container'] = container_id
            self.save_config()

        self.update_bucket_statuses()
        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]

        if bucket['docker']['status'] in ['created', 'exited']:
            # then we can start the container and update status
            success = self.dockerhelper.start_container(bucket['docker']['container'])
            if success:
                self.buckets[ind]['docker']['status'] = 'running'
                self.save_config()
                return True
            else:
                print('ERROR: Failed to start bucket %s' % (bucket['bucket']['name']))
                return False
        else:
            #contained is already running and we should throw an error
            print('ERROR: Bucket %s is already running!' % (bucket['bucket']['name']))
            return False

    def stop_bucket(self,bucket_name):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        self.update_bucket_statuses()
        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]

        if bucket['docker']['status'] in ['running']:
            # then we can start the container and update status
            success = self.dockerhelper.stop_container(bucket['docker']['container'])
            if success:
                self.buckets[ind]['docker']['status'] = 'exited'
                self.save_config()
                return True
            else:
                print('ERROR: Failed to stop bucket %s' % (bucket['bucket']['name']))
                return False
        else:
            #contained is already running and we should throw an error
            print('ERROR: Bucket %s is not running!' % (bucket['bucket']['name']))
            return False

    def execute_command(self,bucket_name,command,detach=True):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        self.update_bucket_statuses()
        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]

        if bucket['docker']['status'] in ['running']:
            # then we can start the container and update status
            result = self.dockerhelper.execute_command(bucket['docker']['container'],command,detach=detach)
            status, output = result
            if (detach and status is None) or (not detach and status==0):
                return True
            else:
                print('ERROR: Failed to execute command %s' % (command))
                return False
        else:
            #contained is already running and we should throw an error
            print('ERROR: Bucket %s is not running!' % (bucket['bucket']['name']))
            return False

    def start_jupyter(self,bucket_name,local_port,container_port):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]
        pid = self.get_jupyter_pid(bucket['docker']['container'])

        if not pid is None:
            port = bucket['docker']['jupyter']['port']
            token = bucket['docker']['jupyter']['token']
            url = 'http://localhost:%s/?token=%s' % (port,token)
            print("Jupyter lab is already running and can be accessed in a browser at: %s" % (url))
            return True


        token = '%048x' % random.randrange(16**48)
        command = "bash -cl 'source activate py36 && jupyter lab --no-browser --ip 0.0.0.0 --port %s --NotebookApp.token=%s --KernelSpecManager.ensure_native_kernel=False'"
        command = command % (container_port, token)

        status = self.execute_command(bucket_name,command,detach=True)
        if status == False:
            return False
        time.sleep(0.1)

        # now check that jupyter is running
        self.update_bucket_statuses()
        pid = self.get_jupyter_pid(bucket['docker']['container'])

        if pid is not None:
            self.buckets[ind]['docker']['jupyter']['token'] = token
            self.buckets[ind]['docker']['jupyter']['port'] = local_port
            self.save_config()
            url = 'http://localhost:%s/?token=%s' % (local_port,token)
            print("Jupyter lab can be accessed in a browser at: %s" % (url))
            time.sleep(3)
            webbrowser.open(url)
            return True
        else:
            print("ERROR: Failed to start jupyter server!")
            return False

    def stop_jupyter(self,bucket_name):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]
        if not bucket['docker']['status'] in ['running']:
            return True

        pid = self.get_jupyter_pid(bucket['docker']['container'])
        if pid is None:
            return True

        port = bucket['docker']['jupyter']['port']
        python_cmd = 'from notebook.notebookapp import shutdown_server, list_running_servers; '
        python_cmd += 'svrs = [x for x in list_running_servers() if x[\\\"port\\\"] == %s]; ' % (port)
        python_cmd += 'sts = True if len(svrs) == 0 else shutdown_server(svrs[0]); print(sts)'
        command = "bash -cl '/home/jovyan/envs/py36/bin/python -c \"%s \"'" % (python_cmd)
        status = self.execute_command(bucket_name,command,detach=False)

        self.update_bucket_statuses()

        # now verify it is dead
        pid = self.get_jupyter_pid(bucket['docker']['container'])
        if not pid is None:
            print("ERROR: Failed to stop jupyter lab.")
            return False

        self.buckets[ind]['docker']['jupyter']['token'] = None
        self.buckets[ind]['docker']['jupyter']['port'] = None
        self.save_config()

        return True

    def export_bucket(self,bucket_name,outfile):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        bucket = self.buckets[ind]
        status = self.dockerhelper.export_container(bucket['docker']['container'], outfile)
        return status

    def import_bucket(self,filename):
        # self.dockerhelper.import_image(filename,name='earthcubeingeo/new-image')

        status = self.create_bucket('new_bucket')
        print(status)
        # status = self.add_image('new_bucket','earthcubeingeo/new-image')
        # print(status)
        ind = self.bucket_names.index('new_bucket')
        self.buckets[ind]['docker']['image'] = 'earthcubeingeo/new-image'
        self.buckets[ind]['docker']['image_id'] = 'sha256:b2ff11cc97eded35057cb680bb048fa78ed3588bd9dfd3de1a168024cfb9b38b'
        self.buckets[ind]['docker']['pull_image'] = None
        self.save_config()
        status = self.add_port('new_bucket',9050,9050,tcp=True)
        print(status)

        status = self.start_bucket('new_bucket')
        print(status)
        status = self.start_jupyter('new_bucket',9050,9050)
        print(status)




    def get_jupyter_pid(self,container):

        result = self.dockerhelper.execute_command(container,'ps -ef',detach=False)
        if result == False:
            return None

        output = result[1].decode('utf-8').split('\n')

        pid = None
        for line in output:
            if ('jupyter-lab' in line or 'jupyter lab' in line) and '--no-browser --ip 0.0.0.0' in line:
                parsed_line = [x for x in line.split(' ') if x != '']
                pid = parsed_line[1]
                break

        return pid

    def update_bucket_statuses(self):
        for i,bucket in enumerate(self.buckets):
            container_id = bucket['docker']['container']
            if container_id is None:
                continue

            status = self.dockerhelper.get_container_status(container_id)
            if status:
                self.buckets[i]['docker']['status'] = status
                self.save_config()

    def get_container(self,bucket_name):
        if not bucket_name in self.bucket_names:
            print("ERROR: Bucket with name: %s does not exist!" % bucket_name)
            return False

        ind = self.bucket_names.index(bucket_name)
        return self.buckets[ind]['docker']['container']

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
