#!/usr/bin/env python
'''
 * Copyright (c) 2018 Spotify AB.
 *
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
'''

import fnmatch
import os
import plistlib
import re
import shutil
import subprocess
import sys

from distutils import dir_util
from nfbuild import NFBuild


class NFBuildOSX(NFBuild):
    def __init__(self):
        super(self.__class__, self).__init__()
        self.project_file = os.path.join(
            self.build_directory,
            'NFDriver.xcodeproj')
        self.clang_format_binary = 'clang-format'
        self.cmake_binary = 'cmake'
        self.android_ndk_folder = '/usr/local/share/android-ndk'
        self.ninja_binary = 'ninja'
        self.ios = False
        self.android = False
        self.android_arm = False

    def generateProject(self,
                        ios=False,
                        android=False,
                        android_arm=False):
        self.use_ninja = android or android_arm
        self.ios = ios
        self.android = android
        self.android_arm = android_arm
        cmake_call = [
            self.cmake_binary,
            '..']
        if android or android_arm:
            android_abi = 'x86_64'
            android_toolchain_name = 'x86_64-llvm'
            if android_arm:
                android_abi = 'arm64-v8a'
                android_toolchain_name = 'arm64-llvm'
            cmake_call.extend([
                '-GNinja',
                '-DANDROID=1',
                '-DCMAKE_TOOLCHAIN_FILE=' + self.android_ndk_folder + '/build/cmake/android.toolchain.cmake',
                '-DANDROID_NDK=' + self.android_ndk_folder,
                '-DANDROID_ABI=' + android_abi,
                '-DANDROID_NATIVE_API_LEVEL=21',
                '-DANDROID_TOOLCHAIN_NAME=' + android_toolchain_name])
            self.project_file = 'build.ninja'
        else:
            cmake_call.extend(['-GXcode'])
        if ios:
            cmake_call.extend(['-DIOS=1'])
        cmake_result = subprocess.call(cmake_call, cwd=self.build_directory)
        if cmake_result != 0:
            sys.exit(cmake_result)

    def buildTarget(self, target, sdk='macosx', arch='x86_64'):
        result = 0
        if self.use_ninja:
            result = subprocess.call([
                self.ninja_binary,
                '-C',
                self.build_directory,
                '-f',
                self.project_file,
                target])
        else:
            result = subprocess.call([
                'xcodebuild',
                '-project',
                self.project_file,
                '-target',
                target,
                '-sdk',
                sdk,
                '-arch',
                arch,
                '-configuration',
                'Release',
                'build'])
        if result != 0:
            sys.exit(result)

    def staticallyAnalyse(self, target, include_regex=None):
        diagnostics_key = 'diagnostics'
        files_key = 'files'
        exceptions_key = 'static_analyzer_exceptions'
        static_file_exceptions = []
        static_analyzer_result = subprocess.check_output([
            'xcodebuild',
            '-project',
            self.project_file,
            '-target',
            target,
            '-sdk',
            'macosx',
            '-configuration',
            'Release',
            '-dry-run',
            'analyze'])
        analyze_command = '--analyze'
        for line in static_analyzer_result.splitlines():
            if analyze_command not in line:
                continue
            static_analyzer_line_words = line.split()
            analyze_command_index = static_analyzer_line_words.index(
                analyze_command)
            source_file = static_analyzer_line_words[analyze_command_index + 1]
            if source_file.startswith(self.current_working_directory):
                source_file = source_file[
                    len(self.current_working_directory)+1:]
            if include_regex is not None:
                if not re.match(include_regex, source_file):
                    continue
            if source_file in self.statically_analyzed_files:
                continue
            self.build_print('Analysing ' + source_file)
            stripped_command = line.strip()
            clang_result = subprocess.call(stripped_command, shell=True)
            if clang_result:
                sys.exit(clang_result)
            self.statically_analyzed_files.append(source_file)
        static_analyzer_check_passed = True
        for root, dirnames, filenames in os.walk(self.build_directory):
            for filename in fnmatch.filter(filenames, '*.plist'):
                full_filepath = os.path.join(root, filename)
                static_analyzer_result = plistlib.readPlist(full_filepath)
                if 'clang_version' not in static_analyzer_result \
                        or files_key not in static_analyzer_result \
                        or diagnostics_key not in static_analyzer_result:
                    continue
                if len(static_analyzer_result[files_key]) == 0:
                    continue
                for static_analyzer_file in static_analyzer_result[files_key]:
                    if static_analyzer_file in static_file_exceptions:
                        continue
                    if self.current_working_directory not in static_analyzer_file:
                        continue
                    normalised_file = static_analyzer_file[
                        len(self.current_working_directory)+1:]
                    if normalised_file in \
                            self.build_configuration[exceptions_key]:
                        continue
                    self.build_print('Issues found in: ' + normalised_file)
                    for static_analyzer_issue in \
                            static_analyzer_result[diagnostics_key]:
                        self.pretty_printer.pprint(static_analyzer_issue)
                        sys.stdout.flush()
                    static_analyzer_check_passed = False
        if not static_analyzer_check_passed:
            sys.exit(1)

    def packageArtifacts(self):
        lib_name = 'libNFDriver.a'
        cli_name = 'NFDriverCLI'
        output_folder = os.path.join(self.build_directory, 'output')
        artifacts_folder = os.path.join(output_folder, 'NFDriver')
        shutil.copytree('include', os.path.join(artifacts_folder, 'include'))
        source_folder = os.path.join(self.build_directory, 'source')
        lib_matches = self.find_file(source_folder, lib_name)
        cli_matches = self.find_file(source_folder, cli_name)
        if not self.android:
            lipo_command = ['lipo', '-create']
            for lib_match in lib_matches:
                lipo_command.append(lib_match)
            lipo_command.extend(['-output', os.path.join(artifacts_folder, lib_name)])
            lipo_result = subprocess.call(lipo_command)
            if lipo_result != 0:
                sys.exit(lipo_result)
        else:
            shutil.copyfile(lib_matches[0], os.path.join(artifacts_folder, lib_name))
        if not self.ios and not self.android:
            shutil.copyfile(cli_matches[0], os.path.join(artifacts_folder, cli_name))
        output_zip = os.path.join(output_folder, 'libNFDriver.zip')
        self.make_archive(artifacts_folder, output_zip)
        if self.android:
            final_zip_name = 'libNFDriver-androidx86.zip'
            if self.android_arm:
                final_zip_name = 'libNFDriver-androidArm64.zip'
            shutil.copyfile(output_zip, final_zip_name)
