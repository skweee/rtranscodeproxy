# rtranscodeproxy
=========

__rtranscodeproxy is a python-based transcoding proxy for HTTP DVB streams on the Raspberry Pie__
 * based on <a href="https://www.raspberrypi.org/forums/viewtopic.php?f=38&t=123876" target="_blank">rtranscode by Guenter Kreidl</a>

__Requirements__
 * Rasbpian Jessie
 * gstreamer-1.0 modules (including gstreamer1.0-omx and omxplayer)
 * python

__Configuration__
 * Set 'rt_proxy_host' and 'rt_proxy_port' to define where the proxy is running
 * Set 'rt_upstream_host' and 'rt_upstream_port' to define the HTTP video source
 * Define your own transcoding settings (defaults are optimized for about 10 MBit)

