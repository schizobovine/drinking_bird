#!/usr/bin/env python
#
# drinking_bird.py - Simulates the infamous drinking bird automation system by
# sending X11 keystroke events to a given window.
#
# Written so I don't have to deal with auto-locking RDP sessions that are also
# behind my regular desktop's screen lock. Trust me, if you're past that,
# you've got the keys to a lot more kingoms than whatever's in that RDP
# session.
#
# And if not, well, guess what? RDP can be automated. Deal. :P

#
# Copyright (C) 2015, Sean Caulfield <sean@yak.net>
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.
#

import sys
import os
import os.path
import re
import warnings
import glob
from time import sleep

import Xlib.X
import Xlib.display
import Xlib.XK
from Xlib.protocol.event import KeyPress, KeyRelease

# These are the environment vars we copy from the window manager to access the X
# server, in case they're not set. Since cron basically gives you no environment
# variables (and really everything but X console login won't set them), this
# allows us to send commands to the X server.
REQ_VARS = (
    'DISPLAY',
    'XAUTHORITY',
)

# Window manager regex, in case one isn't specified as an arg. Matches against
# the process command line (/proc/<pid>/cmdline).
WINDOW_MGR = r'^(?:/usr/bin/)?fluxbox'

# Target window name regex, in case one isn't specified as an arg. Matches
# against what should be the window's title (technically, the WM_NAME property).
TARGET_WIN = '^rdesktop'

# Keys to send, in case they aren't specified as arguments. For info on what
# works here, go hunting for X11 keysyms:
#
# /usr/include/X11/keysymdef.h
# /usr/share/X11/XKeysymDB
#
# (These may be located in other places on your system. I choose a default of
# the Control key by itself, which is usually enough to keep a screensaver from
# activating, while not actually interfering with most actions.
KEYS_TO_SEND = ('Control_L',)

# If non-zero, sleep this number of seconds between keystroke sequences. Seems
# needed since events are non-deterministically ignored even after flushing
# events and trying to force sync() with the X server.
SLEEP_BETWEEN = 0.2

def getRootWindow(xdpy):
    '''Get the root window of the default screen on this X server.'''

    return xdpy.screen().root

def getChildren(window):
    '''Recursive generator that returns the (flat) list of windows that are
    children of the given window. Used to build the filterAllWindows function
    that's the primary search tool.
    '''
    yield window
    children = window.query_tree().children
    for i in children:
        for result in getChildren(i):
            yield result

def filterAllWindows(xdpy, func):
    '''Generic function that recursively searches the window hierarchy starting
    at the root window, returning any window objects the given function eval's
    to True for.
    '''

    root = getRootWindow(xdpy)
    for w in getChildren(root):
        if func(w):
            yield w

# At one point, I thought I needed the actual window object for processing and
# comparison, but turns out I don't. Since getting the object required a goddamn
# search of the window list (really, python-xlib? ugh.), leaving this here for
# posterity.

# def getWindowById(xdpy, winid):
#     def matchWinId(window):
#         return window is not None and window.id == winid
# 
#     found = list(filterAllWindows(xdpy, matchWinId))
#     if found and len(found) > 0:
#         return found[0]
#     else:
#         return None

def getWindowByName(xdpy, regex):
    '''Scan the list of windows for any whose name (WM_NAME property) matches
    the given regular expression.
    '''

    def matchName(window):
        if window:
            name = window.get_wm_name()
            return bool(name and regex.search(name))
        return False

    found = list(filterAllWindows(xdpy, matchName))
    if found and len(found) > 0:
        return found[0]
    else:
        return None

def getActiveWindow(xdpy):
    '''Find the window ID of the currently active window as reported by the
    standard window manager interface for it.

    If your WM doesn't support this, it really sucks.
    '''

    root = getRootWindow(xdpy)
    prop = root.get_full_property(
            xdpy.get_atom('_NET_ACTIVE_WINDOW'),
            Xlib.X.AnyPropertyType,
    )
    if not prop or not hasattr(prop, 'value') or len(prop.value) < 1:
        raise Exception("Couldn't get active window from X server")

    winid = prop.value[0]

    # At one point I thought I needed the actual Window object from Xlib to
    # compare, but turns out only the ID is needed for comparison.
    #window = getWindowById(xdpy, winid)
    #return window

    return winid

def searchProcFsNames(cmdRegex):
    '''Search the /proc filesystem for files whose current command and arguments
    list match the given regex. Normally ps(1) would do this and there's C
    functions for it, but the Python ports all suck.

    Returns the PID of the first matching process.
    Returns the environment varibles (as a dict) of the first matching process
    or None if no match was found.
    '''

    for path in glob.glob('/proc/[1-9]*'):

        cmdline = os.path.join(path, 'cmdline')
        if not os.path.exists(cmdline):
            continue

        try:
            with open(cmdline, 'r') as f:
                cmdstr = f.read()
            if cmdRegex.search(cmdstr):
                try:
                    pid = int(os.path.basename(path))
                    return pid
                except:
                    pass
        except Exception, e:
            print repr(e)

    # Nothing found, raise error
    raise OSError('No process found matching window manager regex')

def getProcEnv(pid):
    '''Given a pid, return a dictionary of the processes environment variables,
    as read from the /proc filesystem.
    '''

    envpath = os.path.join('/proc/', str(pid), 'environ')
    if not os.path.exists(envpath):
        msg = 'Process %d died before environment vars could be read' % pid
        raise OSError(msg)

    with open(envpath, 'r') as f:
        envstr = f.read()

    envvars = envstr.split('\0')
    f = lambda x: isinstance(x, basestring) and '=' in x
    envvars = filter(f, envvars)
    env = dict(i.split('=', 1) for i in envvars)

    return env

def getEnvironment(cmdRegex):
    '''Check if we need to slurp envvars from the given window manager regex,
    and if so, attempt to find the process and grab them.
    '''
    global REQ_VARS

    # We already have them? Skip merrily onward.
    #if all(i in os.environ for i in REQ_VARS):
    #    return {}

    # Otherwise, try to find a WM process & copy its env
    #else:

    pid = searchProcFsNames(cmdRegex)
    env = getProcEnv(pid)
    newEnv = {}

    # Do we have all the required variables to function? Check.
    for var in REQ_VARS:
        if var not in env:
            msg = 'Required environment variable %s not found for pid %d'
            raise LookupError(msg % (var, pid))
        else:
            newEnv[var] = env[var]

    # Qap'la!
    return newEnv

#
# Ugh.
#
# So, in order to send modifier keys (keys you hit in combination with other
# keys--Control, Alt, etc.), we have to lookup if the keycode of the user
# specified keysym in question is currently mapped to a modifer keysym. Using
# that, we can figure out the bitmask needed to pass to XSendEvent.
#
# Perfectly clear, right? >_<
#
# This dictionary identifies which arrays returned from the display object
# (which is just doing whatever XkbGetKeyModifierMap does in C) map to which
# modifier masks.
#

modMapsToMasks = {
    Xlib.X.ShiftMapIndex:   Xlib.X.ShiftMask,
    Xlib.X.LockMapIndex:    Xlib.X.LockMask,
    Xlib.X.ControlMapIndex: Xlib.X.ControlMask,
    Xlib.X.Mod1MapIndex:    Xlib.X.Mod1Mask,
    Xlib.X.Mod2MapIndex:    Xlib.X.Mod2Mask,
    Xlib.X.Mod3MapIndex:    Xlib.X.Mod3Mask,
    Xlib.X.Mod4MapIndex:    Xlib.X.Mod4Mask,
    Xlib.X.Mod5MapIndex:    Xlib.X.Mod5Mask,
}

#
# Allow users to specify these common abbreviations by remapping them to a
# keysym. Arbitrarily picking the left version.
#

friendlyModNames = {
    'ctrl'    : 'Control_L',
    'control' : 'Control_L',
    'alt'     : 'Mod1_L',
    'meta'    : 'Mod3_L',
    'super'   : 'Super_L',
    'windows' : 'Super_L',
    'win'     : 'Super_L',
}

def newKeyEvent(xdpy, window, code, mod, eventClass):
    '''Create a new Xlib.event.KeyPress or Xlib.event.KeyRelease instance, to be
    sent on display XDPY, to window WINDOW, with key code CODE modified by MOD.
    Generic since they're the same damn thing except for the classname. :P
    '''

    # Why the fuck all this shit has to be specified manually and isn't handled
    # by Xlib is a goddamn pain in the ass. It'd be nice if they aren't user
    # specified that the library just assumes some kind of defaults.

    return eventClass(
        detail=code,
        sequence_number=0,
        time=Xlib.X.CurrentTime,
        root=xdpy.screen().root,
        window=window,
        child=Xlib.X.NONE,
        root_x=1,
        root_y=1,
        event_x=1,
        event_y=1,
        state=mod,
        same_screen=True,
    )
    
def newKeyPress(xdpy, window, code, mod):
    '''Calls newKeyEvent with the KeyPress class.'''
    return newKeyEvent(xdpy, window, code, mod, KeyPress)
    
def newKeyRelease(xdpy, window, code, mod):
    '''Calls newKeyEvent with the KeyRelease class.'''
    return newKeyEvent(xdpy, window, code, mod, KeyRelease)

def stringToKeyCode(xdpy, keystr):
    '''Translate the given symbolic key string into the corresponding keycode
    based on the current display's keyboard mappings. Jumps straight over the
    keysym code since we are sending key codes, not symbols. (X11 terminology is
    so clear.)
    '''
    
    # Check for various modifer key aliases
    keystr = friendlyModNames.get(keystr.lower(), keystr)

    # Lookup symbol from static lookups, then find dynamic keycode bound to that
    # symbol
    keysym = Xlib.XK.string_to_keysym(keystr)
    keycode = xdpy.keysym_to_keycode(keysym)

    return keycode

def sendKeyPress(xdpy, window, code, mod):
    '''Send a KeyPress event to the given window, flushing events after.'''

    ev = newKeyPress(xdpy, window, code, mod)
    window.send_event(ev)
    xdpy.flush()

def sendKeyRelease(xdpy, window, code, mod):
    '''Send a KeyRelease event to the given window, flushing events after.'''

    ev = newKeyRelease(xdpy, window, code, mod)
    window.send_event(ev)
    xdpy.flush()

def getModMap(xdpy):
    '''Get which key codes are currently mapped to which modifier masks. If no
    entry is present, the key code isn't bound to a modifier.
    '''
    global modMapsToMasks

    mapping = xdpy.get_modifier_mapping()
    modmap = {}
    for index, keycodes in enumerate(mapping):
        mask = modMapsToMasks.get(index)
        for key in keycodes:
            if key != 0:
                modmap[key] = mask
    return modmap

#
# These functions would be useful if I gave two shits about the existing
# modifiers pressed. For the context of this program, however, it's better to
# just assume no mods pressed and allow any required to be specified.
#

# def keyState(xdpy):
#     for n, bits in enumerate(xdpy.query_keymap()):
#         for i in range(8):
#             k = 8*n + i
#             v = bool(bits & (1<<i))
#             sym = xdpy.keycode_to_keysym(k, 0)
#             s = xdpy.lookup_string(sym)
#             if v:
#                 print k, sym, repr(s)

# def getModifiers(xdpy):
#     modMask = 0
#     for n, bits in enumerate(xdpy.query_keymap()):
#         for i in range(8):
#             k = 8*n + i
#             v = bool(bits & (1<<i))
#             modMask |= modMapsToMasks.get(k, 0)
#     return modMask

#
# Send key events to the target window. There's a little magic here in how to
# send modifier keys:
#
# 1. Get current map of keycodes that are bound to the 8 modifier keys
# 2. Translate key sequence from symbolic strings to the keysyms
# 3. Translate the keysysm to keycodes according to whatever magic the X server
# is using (likely has to do with all that xmodmap and LANG envvar stuff).
# 4. If the keycode is in the modifier map, add it to the modifier mask for the
# key press event. The default of 0 means don't add any modifiers.
#

def sendKeys(xdpy, window, keys, doSleep=0):
    '''Send the iterable of symbolic key strings to the given window. If doSleep
    is non-zero, sleep that number of seconds between each sequence sent.
    '''

    # TODO: Figure out if assuming 0 for the base modifier mask is a safe
    # assumption or if there's some dickery I have to do based on what current
    # modifiers might be pressed. (What happens if you're sending Ctrl+Del and
    # the program runs when you're happening to press Alt?)

    modmap = getModMap(xdpy)
    for key in keys:

        # Ignore any current modifier state.
        modmask = 0
        modkey = None
        modcode = None
        mod = 0

        # If this is a modifier + key combo, send the modifier press event
        # before sending the key. Not entirely necessary, but seems to make
        # things fail less.
        if '+' in key:
            modkey, key = key.split('+')
            modcode = stringToKeyCode(xdpy, modkey)
            mod = modmap.get(modcode, 0)
            modmask |= mod
            sendKeyPress(xdpy, window, modcode, modmask)

        # Lookup key code and if it's a modifier, get the modifier mask
        keycode = stringToKeyCode(xdpy, key)
        keymod = modmap.get(keycode, 0)

        # If this is a modifier, add to modifier mask
        modmask |= keymod

        sendKeyPress(xdpy, window, keycode, modmask)
        sendKeyRelease(xdpy, window, keycode, modmask)

        # Remove added modifier from mask
        modmask &= ~(keymod)

        # If it's a combo, this is non-None and so we should send a release
        # event as well. I guess the program might assume the modifier is being
        # held down by a cat or something.
        if mod:
            modmask &= ~(mod)
            sendKeyRelease(xdpy, window, modcode, 0)

        # Wait a tick between keystrokes in the hope that the target will
        # process the event.
        xdpy.sync()
        if doSleep > 0:
            sleep(doSleep)

def main():

    # Get command line to search for & other params
    cmd = WINDOW_MGR if len(sys.argv) < 2 else sys.argv[1]
    win = TARGET_WIN if len(sys.argv) < 3 else sys.argv[2]
    keys = KEYS_TO_SEND if len(sys.argv) < 4 else sys.argv[3:]

    # Get environment vars
    env = getEnvironment(re.compile(cmd))
    for k, v in env.items():
        os.environ[k] = v

    # Get display & window information
    xdpy = Xlib.display.Display()
    target = getWindowByName(xdpy, re.compile(win))

    # Exit if the target isn't found
    if target is None:
        return

    # Only twiddle if the window isn't the current focus
    #active = getActiveWindow(xdpy).id
    active = getActiveWindow(xdpy)
    if active != target.id:
        sendKeys(xdpy, target, keys)

    # Flush before closing connection to X server to make sure we did our job
    xdpy.flush()
    xdpy.close()

    # Declare mission accomplished and invade Iran...I mean go home.
    sys.exit(0)

if __name__ == '__main__':
    main()
