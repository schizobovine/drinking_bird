#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name='drinking_bird',
    version='1.0.0',
    description='Sends key stroke events to a given X11 window',
    long_description='Sends key stroke events to a given X11 window to save you the trouble of using an actual drinking bird''',
    author='Sean Caulfield',
    author_email='sean@yak.net',
    scripts=['drinking_bird.py', 'twiddle'],
    platforms=('Linux','Unix','POSIX',),
    requires=('Xlib(>=0.14)',),
    license='GPL2',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Operating System :: POSIX',
        'License :: GPL',
    ],
    url='https://github.com/schizobovine/drinking_bird'
)
