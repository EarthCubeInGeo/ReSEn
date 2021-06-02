#!/usr/bin/env python

import gzip
import time
import docker
import requests

# all the docker commands wrapped up nicely

# how do we call docker commands? subprocess? os.call?
# TODO: Use the docker SDK (https://docker-py.readthedocs.io/en/stable/)
class DockerHelper():
    def __init__(self):
        # TODO: define these in a dictionary or json file for each version of resen-core
        # need to get information for each resen-core from somewhere.
        # Info like, what internal port needs to be exposed? Where do we get the image from? etc.
        # mounting directory in the container?
        # What does container.reload() do?  Do we need it?  Where?
        self.container_prefix = 'resen_'

        self.docker = docker.from_env()

    # def create_container(self,**input_kwargs):
    def create_container(self,bucket):
        '''
        Create a docker container with the image, mounts, and ports set in this bucket.  If the image
        does not exist locally, pull it.
        '''

        # set up basic keyword argument dict
        kwargs = dict()
        kwargs['name'] = self.container_prefix + bucket['name']
        kwargs['command'] = 'bash'
        kwargs['tty'] = True
        kwargs['ports'] = dict()

        # if bucket has ports, add these to kwargs
        for host, container, tcp in bucket['port']:
            if tcp:
                key = '%s/tcp' % (container)
            else:
                key = '%s/udp' % (container)
            kwargs['ports'][key] = host

        # if bucket has mounts, add these to kwargs
        kwargs['volumes'] = dict()
        for host, container, permissions in bucket['storage']:
            temp = {'bind': container, 'mode': permissions}
            kwargs['volumes'][host] = temp

        # check if we have image, if not, pull it
        local_image_ids = [x.id for x in self.docker.images.list()]
        if bucket['image']['image_id'] not in local_image_ids:
            # print("Pulling image: %s" % bucket['image']['repo'])
            # print("   This may take some time...")
            # status = self.stream_pull_image(bucket['pull_image'])
            self.stream_pull_image(bucket['image'])
            # image = self.docker.images.get(bucket['pull_image'])
            # repo,digest = pull_image.split('@')
            # # When pulling from repodigest sha256 no tag is assigned. So:
            # image.tag(repo, tag=bucket['image'])
            # print("Done!")

        # start the container
        container = self.docker.containers.create(bucket['image']['image_id'],**kwargs)

        return container.id, container.status


    def remove_container(self,bucket, remove_image=False):
        '''
        Remove the container associated with the provided bucket.
        '''
        container = self.docker.containers.get(bucket['container'])
        container.remove()

        if remove_image:
            self.docker.images.remove(bucket['image']['image_id'])
        return


    def start_container(self, bucket):
        '''
        Start a container.
        '''
        # need to check if bucket config has changed since last run
        container = self.docker.containers.get(bucket['container'])
        container.start()   # this does nothing if already started
        container.reload()
        # print(container.status)
        # time.sleep(0.1)
        # print(container.status)
        return container.status


    def stop_container(self,bucket):
        '''
        Stop a container.
        '''
        container = self.docker.containers.get(bucket['container'])
        container.stop()    # this does nothing if already stopped
        container.reload()
        # time.sleep(0.1)
        return container.status


    def execute_command(self,bucket,command,user='jovyan',detach=True,tty=False):
        '''
        Execute a command in a container.  Returns the exit code and output
        '''
        container = self.docker.containers.get(bucket['container'])
        result = container.exec_run(command,user=user,detach=detach,tty=tty)
        return result.exit_code, result.output


    # def stream_pull_image(self,pull_image):
    def stream_pull_image(self,image):
        '''
        Pull image from dockerhub.
        '''
        import datetime
        import os

        # time formatting
        def truncate_secs(delta_time, fmt=":%.2d"):
            delta_str = str(delta_time).split(':')
            return ":".join(delta_str[:-1]) + fmt%(float(delta_str[-1]))

        # get terminal dimensions
        def get_terminal_dims():
            """Returns integers rows and columns of the terminal."""
            terminal_size = os.get_terminal_size()
            return terminal_size.lines, terminal_size.columns

        # progress bar
        def update_bar(sum_total,accumulated,t0,current_time, init_bar_chars=34):
            """Updates the progress bar.

            Args:
                sum_total: total number of bytes to download
                accumulated: bytes already downloaded
                t0: time when pulling image started
                current_time: current time
                init_bar_chars: default number of characters of the bar
                              including [,>, spaces,  and ],
                              e.g. [===>   ] would be 9 characters.
            """
            percentage = accumulated/sum_total*100
            time_info = "Elapsed t: %s"%truncate_secs(current_time - t0)
            bartext = "%5.1f %%, %5.3f/%4.2fGB %s"%(percentage,
                accumulated/1024**3,sum_total/1024**3,time_info)
            rows, columns = get_terminal_dims()
            max_bar_length = max(5,columns - len(bartext)-1)
            bar_chars = min(init_bar_chars,max_bar_length)
            nchars = int(percentage*(bar_chars-3)/100)
            bar = "\r["+nchars*"="+">"+(bar_chars-3-nchars)*" "+"]"
            total_out = bar+bartext
            max_out = min(columns,len(total_out)) # don't print beyond columns
            print(total_out[:max_out],end="")

        print('Pulling image: {}:{}'.format(image['repo'],image['version']))
        print('   This may take some time...')

        id_list = []
        id_current = []
        id_total = 0
        t0 = prev_time = datetime.datetime.now()
        # define pull_image sha256
        pull_image = '{}/{}@{}'.format(image['org'],image['repo'],image['repodigest'])
        try:
            # Use a lower level pull call to stream the pull
            for line in self.docker.api.pull(pull_image,stream=True, decode=True):
                if 'progress' not in line:
                    continue
                line_current = line['progressDetail']['current']
                if line['id'] not in id_list:
                    id_list.append(line['id'])
                    id_current.append(line_current)
                    id_total += line['progressDetail']['total']
                else:
                    id_current[id_list.index(line['id'])] = line_current
                current_time = datetime.datetime.now()
                if (current_time-prev_time).total_seconds()<1:
                    # To limit print statements to no more than 1 per second.
                    continue
                prev_time = current_time
                update_bar(id_total,sum(id_current),t0,current_time)
            # Last update of the progress bar:
            update_bar(id_total,sum(id_current),t0,current_time)
        except Exception as e:
            raise RuntimeError("\nException encountered while pulling image {}\nException: {}".format(pull_image,str(e)))

        print() # to avoid erasing the progress bar at the end

        # repo,digest = pull_image.split('@')
        # When pulling from repodigest sha256 no tag is assigned. So:
        docker_image = self.docker.images.get(pull_image)
        docker_image.tag('{}/{}'.format(image['org'],image['repo']), tag=image['version'])
        print("Done!")

        return

    def export_container(self,bucket,filename,repo,tag):
        '''
        Export existing container to a tared image file.  After tar file has been created, image of container is removed.
        '''

        # TODO:
        # Add checks that image was sucessfully saved before removing it?

        container = self.docker.containers.get(bucket['container'])

        # set a long timeout for this - image save takes a while
        default_timeout = self.docker.api.timeout
        self.docker.api.timeout = 60.*60.*24.

        # image_name = '{}:{}'.format(repo,tag)
        full_repo = '{}/{}'.format(bucket['image']['org'],repo)
        image_name = '{}:{}'.format(full_repo,tag)

        try:
            # create new image from container
            container.commit(repository=full_repo,tag=tag)

            # save image as *.tar file
            image = self.docker.images.get(image_name)
            out = image.save()
            # with open(str(filename), 'wb') as f:
            with gzip.open(str(filename),'wb',compresslevel=1) as f:
                for chunk in out:
                    f.write(chunk)

        except requests.exceptions.ReadTimeout:
            raise RuntimeError('Timeout while exporting bucket!')

        finally:
            # remove image after it has been saved or if a timeout occurs
            self.docker.images.remove(image_name)

            # reset default timeout
            self.docker.api.timeout = default_timeout

        return

    def import_image(self,filename,repo,tag):
        '''
        Import an image from a tar file.  Return the image ID.
        '''

        with open(str(filename), 'rb') as f:
            image = self.docker.images.load(f)[0]

        # add tag
        image.tag(repo, tag)

        return image.id

    def get_container_size(self, bucket):
        # determine the size of the container (disk space)
        # docker container inspect (https://docs.docker.com/engine/reference/commandline/container_inspect/) should be able to be used
        #   for this purpose, but it looks like the docker SDK equivilent (APIClient.inspect_container()) does not include fuctionality
        #   for the --size flag (https://docker-py.readthedocs.io/en/stable/api.html#module-docker.api.container), so the dict returned
        #   does not have size information

        # with docker.APIClient() as apiclient:
        #     info = apiclient.containers(all=True, size=True, filters={'id':bucket['container']})[0]
        info = self.docker.api.containers(all=True, size=True, filters={'id':bucket['container']})[0]

        return info['SizeRw']+info['SizeRootFs']

    def get_container_status(self, bucket):
        '''
        Get the status of a particular container.
        '''
        container = self.docker.containers.get(bucket['container'])
        container.reload()  # maybe redundant

        return container.status

    # # get a container object given a container id
    # def get_container(self,container_id):
    #     try:
    #         container = self.docker.containers.get(container_id)
    #         return container
    #     except docker.errors.NotFound:
    #         print("ERROR: No such container: %s" % container_id)
    #         return None
