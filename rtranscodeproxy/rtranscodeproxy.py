#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Transcoding proxy for http TS streams from your local TV-server
# It requires Raspbian Jessie and a full set of gstreamer-1.0 modules including the gstreamer1.0-omx module
#
# Copyright 2017 by Johannes Tysiak
#
# Based on rtranscode from the transcoder package 3.0 
# https://www.raspberrypi.org/forums/viewtopic.php?f=38&t=123876
# Copyright 2015-2017 by Guenter Kreidl
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

import os,signal,sys,curses,subprocess,time,re
import gi,BaseHTTPServer,SocketServer,getpass
from threading import Timer

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gio, Gst

## Proxy settings.

## Set the host and port for the transcoding proxy
rt_proxy_host = 'raspberrypi'
rt_proxy_port = 8001

## Specify the hostname of the video source (upstream)
rt_upstream_host = 'videosource'
rt_upstream_port = 8001

## Default transcoding settings. Modify for your specific needs. 

## Part 1: The following should only be changed, if you really know what you are doing:

rt_sd_video_sizes = {'0':"120x96",'1':"180x144",'2':"240x192",'3':"360x288",'4':"480x384",'5':"600x480",'6':"720x576"}

rt_hd_video_sizes = {'0':"256x144",'1':"512x288",'2':"768x432",'3':"910x512",'4':"1024x576",'5':"1280x720"}

rt_sd_modes = {'sd1':{'videoparser':'mpegvideoparse','videodecoder':'omxmpeg2videodec','framerate':'25','check':['mpeg2video','x576','25 fps']},
            'sd2':{'videoparser':'h264parse','videodecoder':'omxh264dec','framerate':'25','check':['h264','x576','25 fps']}
            }

rt_hd_modes = {'hd1':{'videoparser':'h264parse','videodecoder':'omxh264dec','framerate':'50','check':['h264','50 fps']},
            'hd2':{'videoparser':'h264parse','videodecoder':'omxh264dec','framerate':'25','check':['h264','25 fps']}
            }

rt_audio_decoders ={'mpeg':{'audioparser':'mpegaudioparse','audiodecoder':'mpg123audiodec','format':'MPEG'},
                'ac3':{'audioparser':'ac3parse','audiodecoder':'a52dec','format':'AC3'},
                'aac':{'audioparser':'aacparse','audiodecoder':'avdec_aac','format':'AAC'}
                }

rt_aac_encoder ='avenc_aac compliance=-2'

rt_ac3_encoder = 'avenc_ac3 compliance=-2'


# The gstreamer tool chains to be used.
# For both video and audio transcoding:
rt_av_template = 'souphttpsrc location="$uri$" is-live=true keep-alive=true do-timestamp=true retries=10 typefind=true blocksize=16384 name=source ! tsdemux parse-private-sections=false name=demux demux.audio$apid$ ! queue ! $audioparser$ ! $audiodecoder$ ! audioconvert dithering=0 ! audio/x-raw,channels=$channels$ ! $audioencoder$ bitrate=$abr$ ! matroskamux name=mux streamable=true demux. ! queue ! $videoparser$ ! $videodecoder$ name=dec ! omxh264enc target-bitrate=$vbr$ control-rate=variable ! video/x-h264,profile=high,width=$width$,height=$height$,framerate=$framerate$/1 ! h264parse ! queue ! mux. mux. ! multisocketsink name=sink'

# For video transcoding only, using one of the original audio tracks:
rt_v_template = 'souphttpsrc location="$uri$" is-live=true keep-alive=true do-timestamp=true retries=10 typefind=true blocksize=16384 name=source ! tsdemux parse-private-sections=false name=demux demux.audio$apid$ ! queue ! $audioparser$ ! matroskamux name=mux streamable=true demux. ! queue ! $videoparser$ ! $videodecoder$ name=dec ! omxh264enc target-bitrate=$vbr$ control-rate=variable ! video/x-h264,profile=high,width=$width$,height=$height$,framerate=$framerate$/1 ! h264parse ! queue ! mux. mux. ! multisocketsink name=sink'

## Part 2; Default values which you might want to (or should) change:

## The following settings take indices into the dictionaries above as entries
rt_sd_video_size = '6'
rt_hd_video_size = '2'
rt_video_bitrate = '750000'
rt_audio_bitrate = '65536' # set to 0 for original audio track

## start value for using 6ch ac3 audio out, by default 384K and should not be lower than 192K
rt_ch6limit = '393216'
## Use 6 channels for AAC output too, if True
aac_6ch = False

## set the following to 'ac3' if you want always transcode audio to AC3, or to 'aac' to always get AAC
## 'both' means, that it depends on the input format 
rt_audiooutput = 'both'

## desired audio languages (listed by priority)
rt_audiolangs = ['eng', 'deu', 'qad', 'qaa']

## end of global preset settings

# Initialize the gstreamer toolchain
Gst.init(None)
GObject.threads_init()

# reopen stdout in non-buffered mode
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

aformat = ''
oaformat = ''
dimension = ''

def compile_pipeline(args):
    global dimension,aformat,oaformat
    oaformat = ''
    aformat = ''
    abr = rt_audio_bitrate
    if abr == '0':
        script = rt_v_template
    else:
        script = rt_av_template
    uri = args[0]
    vmode = args[1]
    vmodes = rt_sd_modes.keys() + rt_hd_modes.keys()
    if vmode not in vmodes:
        raise Exception('no valid videomode')
    amode = args[2]
    if amode not in rt_audio_decoders.keys():
        raise Exception('no valid audiomode')
    audiopid = args[3]
    if audiopid == '-1':
        apid = ''
    else:
        base = 10
        if audiopid.startswith('0x'):
            base = 16
        try:
            apid = '_' + hex(int(audiopid,base)).split('x')[1].rjust(4,'0')
        except:
            raise Exception('no valid audiopid')
    if vmode in rt_sd_modes.keys():
        wxh = rt_sd_video_sizes[rt_sd_video_size].split('x')
        dimension = rt_sd_video_sizes[rt_sd_video_size]
    else:
        wxh =  rt_hd_video_sizes[rt_hd_video_size].split('x')
        dimension = rt_hd_video_sizes[rt_hd_video_size]
    width = wxh[0]
    height = wxh[1]
    if vmode in rt_sd_modes.keys():
        videoparser = rt_sd_modes[vmode]['videoparser']
        videodecoder = rt_sd_modes[vmode]['videodecoder']
        framerate = rt_sd_modes[vmode]['framerate']
    else:
        videoparser = rt_hd_modes[vmode]['videoparser']
        videodecoder = rt_hd_modes[vmode]['videodecoder']
        framerate = rt_hd_modes[vmode]['framerate']

    audioparser = rt_audio_decoders[amode]['audioparser']
    audiodecoder = rt_audio_decoders[amode]['audiodecoder']
    oaformat = rt_audio_decoders[amode]['format']
    vbr = rt_video_bitrate
    if rt_audiooutput == 'ac3':
        audioencoder = rt_ac3_encoder
        aformat = 'AC3'
    elif rt_audiooutput == 'aac':
        audioencoder = rt_aac_encoder
        aformat = 'AAC'
    else:
        if amode == 'ac3':
            audioencoder = rt_ac3_encoder
            aformat = 'AC3'
        else:
            audioencoder = rt_aac_encoder
            aformat = 'AAC'

    channels = '2'
    if int(abr) < 64000:
        channels = '1'
    elif aformat == 'AC3' and int(abr) >= int(rt_ch6limit):
        channels = '6'
    elif aformat == 'AAC' and aac_6ch and int(abr) >= int(rt_ch6limit):
        channels = '6'
    # create script:
    script = script.replace('$channels$',channels)
    script = script.replace('$uri$',uri)
    script = script.replace('$apid$',apid)
    script = script.replace('$audioparser$',audioparser)
    script = script.replace('$audiodecoder$',audiodecoder)
    script = script.replace('$audioencoder$',audioencoder)
    script = script.replace('$abr$',abr)
    script = script.replace('$videoparser$',videoparser)
    script = script.replace('$videodecoder$',videodecoder)
    script = script.replace('$vbr$',vbr)
    script = script.replace('$width$',width)
    script = script.replace('$height$',height)
    script = script.replace('$framerate$',framerate)
    return script

def get_omxplayer_info(uri):
    currentuser = getpass.getuser()
    try:
        omxplayer_env = os.environ.copy()
        omxplayer_env["USER"] = currentuser
        db = subprocess.Popen(['/usr/bin/omxplayer','-i',uri],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=omxplayer_env)
        (res,err) = db.communicate()
    except:
        return ''
    pid = None
    pidfile = None
    try:
        pidfile = open('/tmp/omxplayerdbus.'+currentuser+'.pid', 'r')
    except IOError, exc:
        pass
    if pidfile:
        line = pidfile.readline().strip()
        try:
            pid = int(line)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        except ValueError:
            pass
        pidfile.close()
    os.remove('/tmp/omxplayerdbus.'+currentuser)
    os.remove('/tmp/omxplayerdbus.'+currentuser+'.pid')
    return res

def analyze_uri(uri):
    mode = ''
    amode = ''
    apid = ''
    mpegapids = {}
    ac3pids = {}
    aacpids = {}
    amodes = []
    tsstream = False
    res = get_omxplayer_info(uri)
    if not res:
        print 'No result from omxplayer'
        return (mode,amode,apid)
    rl = res.split('\n')
    for line in rl:
        if 'Input #0' in line and 'mpegts' in line:
            tsstream = True
            break
    if not tsstream:
        return (mode,amode,apid)
    for ind in range(0,len(rl)):
        if 'Stream #0:' in rl[ind] and 'Video:' in rl[ind]:
            for k in rt_sd_modes.keys():
                chflag = True
                for ch in rt_sd_modes[k]['check']:
                    if ch not in rl[ind]:
                        chflag = False
                        break
                if chflag:
                    mode = k
                    break
            if not mode:
                for k in rt_hd_modes.keys():
                    chflag = True
                    for ch in rt_hd_modes[k]['check']:
                        if ch not in rl[ind]:
                            chflag = False
                            break
                    if chflag:
                        mode = k
                        break
    if not mode:
        return (mode,amode,apid)
    for line in rl:
        if 'Stream #0:' in line and 'Audio:' in line:
            ap = line.split(':')[1]
            reg = re.compile('0x[0-9,a-f]+')
            langreg = re.compile('\]\((.*)\)')
            regres = reg.search(ap)
            langregres = langreg.search(ap)
            if langregres:
                pidlang = langregres.group(1)
            else:
                pidlang = '(unknown)'
            if regres :
                pid = regres.group()
                if pid and 'mp2' in line:
                    mpegapids[pidlang] = pid
                elif pid and 'ac3' in line:
                    ac3pids[pidlang] = pid
                elif pid and 'aac' in line:
                    aacpids[pidlang] = pid
            else:
                if 'mp2' in line:
                    amodes.append('mpeg')
                elif 'ac3' in line:
                    amodes.append('ac3')
                elif 'aac' in line:
                    amodes.append('ac3')
    if len(mpegapids) == len(ac3pids) == len(aacpids) == 0 and len(amodes) == 1:
        apid = '-1'
        amode = amodes[0]
    langs = rt_audiolangs
    if mpegapids and not (ac3pids and rt_audiooutput == 'ac3'):
        for lang in reversed(langs):
            if lang in mpegapids: apid = mpegapids[lang]
        if not apid: apid = mpegapids[next(iter(mpegapids))]
        amode = 'mpeg'
    elif ac3pids:
        for lang in reversed(langs):
            if lang in ac3pids: apid = ac3pids[lang]
        if not apid: apid = ac3pids[next(iter(ac3pids))]
        amode = 'ac3'
    elif aacpids:
        for lang in reversed(langs):
            if lang in aacpids: apid = aacpids[lang]
        if not apid: apid = aacpids[next(iter(aacpids))]
        amode = 'aac'
    return (mode,amode,apid)

class gst_broadcaster:

    def __init__(self, gpipeline):
        self.pipeline = Gst.parse_launch(gpipeline)
        self.source = self.pipeline.get_by_name('source')
        self.enc = self.pipeline.get_by_name('enc')
        self.mux = self.pipeline.get_by_name('mux')
        self.sink = self.pipeline.get_by_name('sink')
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message',self.message_handler)
        self.sink.connect('client-added',self.add_client)
        self.sink.connect('client-socket-removed',self.remove_client)
        self.sink.set_property('timeout',30000000000)
        self.clients = []
        self.pipeline.set_state(Gst.State.READY)


    def kill(self):
        self.pipeline.set_state(Gst.State.NULL)


    def handles(self):
        return self.sink.get_property('num-handles')

    def add_client(self,sink,gsock):
        print 'Adding client socket:', gsock
        self.pipeline.set_state(Gst.State.PLAYING)
        self.clients.append(gsock)

    def remove_client(self,sink,gsock):
        print 'Removing socket:',gsock
        self.sink.emit('remove-flush',gsock)
        if gsock in self.clients:
            self.clients.remove(gsock)

    def handle(self, wfile):
        client_id = Gio.Socket().new_from_fd(wfile.fileno())
        self.sink.emit("add", client_id)
        while client_id in self.clients:
            time.sleep(1)

    def cleanup(self,client_addr):
        if client_addr in self.clients:
            self.clients.remove(client_addr)

    def message_handler(self,bus,message):
        msgType = message.type
        if msgType == Gst.MessageType.ERROR:
            self.kill()
            print "\n Unable to play Video. Error: ", \
            message.parse_error()
        elif msgType == Gst.MessageType.EOS:
            self.kill()

class MyHTTPHandler(BaseHTTPServer.BaseHTTPRequestHandler):

    def do_HEAD(s):
        s.send_response(200)
        s.send_header("Content-type", "text/html")
        s.end_headers()
    def do_GET(s):
        s.send_response(200)
        s.send_header("Content-type", "video/video/x-matroska")
        s.end_headers()
        path = s.path
        print 'New request for: ' + path
        uri = 'http://' + rt_upstream_host + ':' + str(rt_upstream_port) + path
        mode,amode,apid = analyze_uri(uri)
        print 'uri: ' +  uri 
        print 'mode: ' + mode
        print 'amode: ' + amode
        print 'apid: ' + apid
        pipeline = compile_pipeline([uri,mode,amode,apid])
        print 'pipeline: ' + pipeline
        player =  gst_broadcaster(pipeline)
        try:
            player.handle(s.wfile)
        except:
            s.wfile = StringIO()
        print 'Destroying gstreamer pipeline'
        player.kill()
        return


class ThreadedHTTPServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    """Handle requests in a separate thread."""

httpd = ThreadedHTTPServer((rt_proxy_host, rt_proxy_port), MyHTTPHandler)

def httpd_start():
    print time.asctime(), "Server Starts - %s:%s" % (rt_proxy_host, rt_proxy_port)
    httpd.serve_forever()

def httpd_shutdown():
    httpd.server_close()
    print time.asctime(), "Server Stops - %s:%s" % (rt_proxy_host, rt_proxy_port)

def signal_handler(signal, frame):
    try:
        httpd_shutdown()
    finally:
      sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        httpd_start()
    except KeyboardInterrupt:
        pass
    httpd_shutdown()
