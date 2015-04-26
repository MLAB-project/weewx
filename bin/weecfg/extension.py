#
#    Copyright (c) 2009-2015 Tom Keffer <tkeffer@gmail.com> and
#                            Matthew Wall
#
#    See the file LICENSE.txt for your full rights.
#
"""Utilities for installing and removing extensions"""

# The names of some root directories. For the extension pmon,
#    -user/                          # The USER_ROOT subdirectory
#    -user/installer/                # The EXT_ROOT subdirectory
#    -user/installer/pmon/           # The extension's installer subdirectory
#    -user/installer/pmon/install.py # The copy of the installer for the extension

import os
import shutil
import sys

import weecfg
from weecfg import Logger
from weewx import all_service_groups
import weeutil.weeutil

class InstallError(Exception):
    """Exception thrown when installing an extension."""

class ExtensionInstaller(dict):
    """Base class for extension installers."""

class ExtensionEngine(object):
    """Engine that manages extensions."""
    # Extension components can be installed to these locations
    target_dirs = {
        'bin': 'BIN_ROOT',
        'skins': 'SKIN_ROOT'}

    def __init__(self, config_path, config_dict, tmpdir=None, bin_root=None, 
                 dry_run=False, logger=None):
        """Initializer for ExtensionEngine. 
        
        config_path: Path to the configuration file. (Something like /home/weewx/weewx.conf)

        config_dict: The configuration dictionary (the contents of the file at config_path).

        tmpdir: A temporary directory to be used for extracting tarballs and the like [Optional]

        bin_root: A path to the root of the weewx binary files (Something like /home/weewx/bin).
        Optional. If not given, it will be determined from the location of this file.
        
        dry_run: If True, all the steps will be printed out, but nothing will actually be done.
        
        logger: An instance of weecfg.Logger. This will be used to print things to the console.        
        """
        self.config_path = config_path
        self.config_dict = config_dict
        self.logger = logger or Logger()
        self.tmpdir = tmpdir or '/var/tmp'
        # BIN_ROOT does not normally appear in the configuration dictionary. Set a
        # default (which could be 'None')
        self.config_dict.setdefault('BIN_ROOT', bin_root)
        self.dry_run = dry_run

        self.root_dict = weecfg.extract_roots(self.config_path, self.config_dict)
        self.logger.log("root dictionary: %s" % self.root_dict, 4)
        
    def enumerate_extensions(self):
        """Print info about all installed extensions to the logger."""
        ext_root = self.root_dict['EXT_ROOT']
        try:
            exts = os.listdir(ext_root)
            if exts:
                for f in exts:
                    self.logger.log(f, level=0)
            else:
                self.logger.log("Extension cache is '%s'" % ext_root, level=2)
                self.logger.log("No extensions installed", level=0)
        except OSError:
            self.logger.log("No extension cache '%s'" % ext_root, level=2)
            self.logger.log("No extensions installed", level=0)
            
    def install_extension(self, extension_path):
        """Install the extension that can be found at a given path."""
        self.logger.log("Request to install '%s'" % extension_path)
        if os.path.isfile(extension_path):
            # It's a file, hopefully a tarball. Extract it, then install
            try:
                member_names = weecfg.extract_tarball(extension_path, self.tmpdir, self.logger)
                extension_reldir = os.path.commonprefix(member_names)
                if extension_reldir == '':
                    raise InstallError("No common path in tarfile '%s'. Unable to install." % extension_path)
                extension_dir = os.path.join(self.tmpdir, extension_reldir)
                self.install_from_dir(extension_dir)
            finally:
                shutil.rmtree(extension_dir, ignore_errors=True)
        elif os.path.isdir(extension_path):
            # It's a directory, presumably containing the extension. Install directly
            self.install_from_dir(extension_path)
        else:
            raise InstallError("Extension '%s' not found or cannot be identified." % extension_path)
    
        self.logger.log("Finished installing extension '%s'" % extension_path)

    def install_from_dir(self, extension_dir):
        """Install the extension that can be found in a given directory"""
        self.logger.log("Request to install extension found in directory %s" % extension_dir, level=2)

        # The "installer" is actually a dictionary containing what is to be installed and
        # where. The "installer_path" is the path to the file containing that dictionary.        
        installer_path, installer = weecfg.get_extension_installer(extension_dir)
        extension_name = installer.get('name', 'Unknown')
        self.logger.log("Found extension with name '%s'" % extension_name, level=2)

        # Go through all the files used by the extension. A "source tuple" is something
        # like (bin, [user/myext.py, user/otherext.py]). The first element is the
        # directory the files go in, the second element is a list of files to be put
        # in that directory
        self.logger.log("Copying new files.", level=2)
        N = 0
        for source_tuple in installer['files']:
            # For each set of sources, check and see if it's a type we know about
            for directory in ExtensionEngine.target_dirs:
                # This will be something like 'bin', or 'skins':
                source_type = os.path.commonprefix((source_tuple[0], directory))
                # If there is a match, source_type will be something other than an empty string:
                if source_type:
                    # This will be something like 'BIN_ROOT' or 'SKIN_ROOT':
                    root_type = ExtensionEngine.target_dirs[source_type]
                    # Now go through all the files of the source tuple
                    for install_file in source_tuple[1]:
                        source_path = os.path.join(extension_dir, install_file)
                        destination_path = os.path.abspath(os.path.join(self.root_dict[root_type], '..', install_file))
                        self.logger.log("Copying from '%s' to '%s'" % (source_path, destination_path), level=3)
                        if not self.dry_run:
                            try:
                                os.makedirs(os.path.dirname(destination_path))
                            except OSError:
                                pass
                            shutil.copy(source_path, destination_path)
                            N += 1
                    break
            else:
                sys.exit("Unknown destination for file %s" % source_tuple)
        self.logger.log("Copied %d files" % N, level=2)
        
        save_config = False
        new_top_level = []
        # Look for options that have to be injected into the configuration file
        if 'config' in installer:
            self.logger.log("Adding new sections to configuration file", level=2)
            # Remember any new top-level sections (so we can inject a major comment block):
            for top_level in installer['config']:
                if top_level not in self.config_dict:
                    new_top_level.append(top_level)

            if not self.dry_run:
                # Inject any new config data into the configuration file
                weecfg.conditional_merge(self.config_dict, installer['config'])
                
                # Now include the major comment block for any new top level sections
                for new_section in new_top_level:
                    self.config_dict.comments[new_section] = weecfg.major_comment_block + \
                                ["# Options for extension '%s'" % extension_name]
                    
                save_config = True
        
        # Go through all the possible service groups and see if the extension provides
        # a new one
        self.logger.log("Adding new services.", level=2)
        for service_group in all_service_groups:
            if service_group in installer:
                extension_svcs = weeutil.weeutil.option_as_list(installer[service_group])
                for svc in extension_svcs:
                    # See if this service is already in the service group
                    if svc not in self.config_dict['Engine']['Services'][service_group]:
                        if not self.dry_run:
                            # Add the new service into the appropriate service group
                            self.config_dict['Engine']['Services'][service_group].append(svc)
                            save_config = True
                        self.logger.log("Added new service %s to %s" %(svc, service_group))

        # Save the extension's install.py file in the extension's installer directory
        extension_installer_dir = os.path.join(self.root_dict['EXT_ROOT'], extension_name)
        self.logger.log("Saving installer file to %s" % extension_installer_dir)
        if not self.dry_run:
            try:
                os.makedirs(os.path.join(extension_installer_dir))
            except OSError:
                pass
            shutil.copy2(installer_path, extension_installer_dir)
                                
        if save_config:
            backup_path = weecfg.save_with_backup(self.config_dict, self.config_path)
            self.logger.log("Saved configuration dictionary. Backup copy at %s" % backup_path)
            
    def uninstall_extension(self, extension_name):
        """Uninstall the extension with name extension_name"""
        
        self.logger.log("Request to remove extension with name %s" % extension_name, level=2)
        
        # Find the subdirectory containing this extension's installer
        extension_installer_dir = os.path.join(self.root_dict['EXT_ROOT'], extension_name)
        # Retrieve it
        _, installer = weecfg.get_extension_installer(extension_installer_dir)

        # Remove any files that were added:
        self.uninstall_files(installer)
                
        save_config = False

        # Remove any services we added
        for service_group in all_service_groups:
            if service_group in installer:
                new_list = filter(lambda x : x not in installer[service_group], 
                                  self.config_dict['Engine']['Services'][service_group])
                if not self.dry_run:
                    self.config_dict['Engine']['Services'][service_group] = new_list
                    save_config = True
        
        # Remove any sections we added
        if 'config' in installer and not self.dry_run:
            weecfg.remove_and_prune(self.config_dict, installer['config'])
            save_config = True
        
        if not self.dry_run:
            # Finally, remove the extension's installer subdirectory:
            shutil.rmtree(extension_installer_dir)
        
        if save_config:
            weecfg.save_with_backup(self.config_dict, self.config_path)
            
    def uninstall_files(self, installer):
        """Delete files that were installed for this extension"""

        # First remove any bin files the extension might have added:
        self.logger.log("Removing files.", level=2)
        N = 0
        for source_tuple in installer['files']:
            # For each set of sources, check and see if it's a type we know about
            for directory in ExtensionEngine.target_dirs:
                # This will be something like 'bin', or 'skins':
                source_type = os.path.commonprefix((source_tuple[0], directory))
                # If there is a match, source_type will be something other than an empty string:
                if source_type:
                    # This will be something like 'BIN_ROOT' or 'SKIN_ROOT':
                    root_type = ExtensionEngine.target_dirs[source_type]
                    # Now go through all the files of the source tuple
                    for install_file in source_tuple[1]:
                        destination_path = os.path.abspath(os.path.join(self.root_dict[root_type], '..', install_file))
                        N += self.delete_file(destination_path)
                        N += self.delete_file(destination_path.replace('.py', 'pyc'), False)
                        N += self.delete_file(destination_path.replace('.py', 'pyo'), False)
                    # If the directory is empty, delete it
                    directory = os.path.abspath(os.path.join(self.root_dict[root_type], '..', source_tuple[0]))
                    self.delete_directory(directory)
                    break
            else:
                sys.exit("Unknown destination for file %s" % source_tuple)
        self.logger.log("Removed %d files" % N, level=2)
        
    def delete_file(self, filename, report_errors=True):
        """Delete the given file from the file system.

        filename: The path to the file to be deleted.
        
        report_errors: If true, report an error if the file is
        missing or cannot be deleted. Otherwise don't. In
        neither case will an exception be raised. """
        try:
            self.logger.log("Deleting file %s" % filename, level=2)
            if not self.dry_run:
                os.remove(filename)
                return 1
        except OSError, e:
            if report_errors:
                self.logger.log("Delete failed: %s" % e, level=4)
        return 0

    def delete_directory(self, directory, report_errors=True):
        """Delete the given directory from the file system.

        directory: The path to the directory to be deleted. If the
        directory is not empty, nothing is done.
        
        report_errors; If true, report an error. Otherwise don't. In
        neither case will an exception be raised. """
        try:
            if os.listdir(directory):
                self.logger.log("Directory '%s' not empty" % directory, level=2)
            else:
                self.logger.log("Deleting directory %s" % directory, level=2)
                if not self.dry_run:
                    shutil.rmtree(directory)
        except OSError, e:
            if report_errors:
                self.logger.log("Delete failed on directory '%s': %s" % (directory, e), level=2)
        