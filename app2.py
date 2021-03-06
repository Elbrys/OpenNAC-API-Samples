#!/usr/bin/env python
"""
DESCRIPTION:
    This is an extremely simple Python application that demonstrates how to use Elbrys SDN Developer Lab (dev.elbrys.com) to
    control endpoint user sessions access to the network.

    This application will connect to one of the switches that you have connected in sdn-developer.elbrys.com
    and demonstrate blocking and unblocking of network traffic for any device connected to the switch. 

PRE-REQUISITES:
   1.  Python 2.x
   2.  Install python-requests:
        a.  sudo easy_install requests
   3.  Go to dev.elbrys.com and follow the directions there


Mail bug reports and suggestion to : support@elbrys.com
"""

import sys, os, errno, signal 
import requests
import json
import time
import argparse
from requests.auth import HTTPBasicAuth

def interuppt_handler(signum, frame):
    sys.exit(-2) #Terminate process here as catching the signal removes the close process behaviour of Ctrl-C

signal.signal(signal.SIGINT, interuppt_handler)

 
def GetAuthToken(user, password, parser):
    global odlsBaseUrl
    # This calls the  api to create an authorization token to make other calls
    # RETURNS: authorization token
    url = odlsBaseUrl + '/auth/token'
    headers = {'content-type': 'application/json'}
    user = "name="+user
    appId = requests.get(url, headers=headers, auth=HTTPBasicAuth(user,password))
    result = appId.text
    status = appId.status_code
    if ((status >= 200) & (status <=299)):
        authToken = appId.json()
        authToken = authToken['token']
    else:
        print " "
        print "!! Error !!"  
        print "    Unable to create authorization token.  Double check that the username and password you entered."
        print "    See usage below:"
        parser.print_help()
        sys.exit()

    return authToken;

def GetApps(authToken):
    global odlsBaseUrl
    url = odlsBaseUrl + '/applications'
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    r = requests.get(url, headers=headers)
    if ((r.status_code < 200) | (r.status_code > 299)):
        print "Error getting applications list: " + r.text
        sys.exit()
    else:
        return r

def GetAppInfo(authToken, appId):
    global odlsBaseUrl
    url = odlsBaseUrl + '/applications/' + appId
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    r = requests.get(url, headers=headers)
    if ((r.status_code < 200) | (r.status_code > 299)):
        print "Error getting application info: " + r.text
        sys.exit()
    else:
        return r

def RemoveZombieApps(authToken, switch):
    # Removes any old applications currently connected to the target switch.  Only
    # one application may be connected to a switch.
    apps = GetApps(authToken)
    for a in apps.json():
        appInfo = GetAppInfo(authToken, a['id'])
        appInfo = appInfo.json()
        appScope = appInfo['scope']
        appVnets = appScope['vnets']
        for v in appVnets:
            if (v == switch):
                print "Deleting a zombie application: " + a['id'] + ", " + a['name']
                DeleteApp(authToken,a['id'])
                break

def CreateApp(authToken, switch, parser):
    global odlsBaseUrl
    # This removes any zombie apps and then calls the api to create an application
    # RETURNS: app identifier
    RemoveZombieApps(authToken, switch)

    url = odlsBaseUrl + '/applications'
    payload = {'name': 'Demo App2 - Connected to switch: ' + switch,
                'scope': {'vnets':[switch]}}
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    appId = requests.post(url, data=json.dumps(payload), headers=headers)
    result = appId.text
    status = appId.status_code
    if ((status >= 200) & (status <=299)):
        appId = appId.json()
        appId = appId['id']
    else:
        print " "
        print "!! Error !!"  
        print "    Unable to create application.  Double check your switch identifier."
        print "    See usage below:"
        parser.print_help()
        sys.exit()

    return appId;


def CreateUnblockPolicy(authToken, appId):
    global odlsBaseUrl
    # This calls the  api to create an authenticated
    # policy for the application.  
    # This is the policy that a new endpoint will
    # be given.
    # This policy will:
    #    - allow any packet to pass
    # RETURNS: app identifier
    # Now create authenticated policy using network resource
    # NOTE: default policy is False.  If you want to see 'unmanaged endpoint'
    #       events in subscription stream then you cannot have a default policy
    #       defined.
    url = odlsBaseUrl + '/applications/' + appId + '/policies'
    payload = {
               'name': 'unblocked',
               'default': False,
               'rules': [
                         {
                          'actions': [
                                        {'type': 'pass'}
                                     ]
                         }
                        ]
              }
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}

    r = requests.post(url, data=json.dumps(payload), headers=headers)

    # print "here 5" + r.status_code
    status = r.status_code
    if ((status >= 200) & (status <=299)):
        policyId = r.json()
        policyId = policyId['id']
    else:
        print " "
        print "!! Error !!"  
        print "    Unable to create unblock policy."
        sys.exit()

    return policyId;


def CreateSubscription(authToken, appId):
    global odlsBaseUrl
    # This calls the api to create a subscription
    # RETURNS: subscription identifier
    url = odlsBaseUrl + '/applications/' + appId + '/subscriptions'
    payload = {'type': 'httpSSE'}
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    subId = requests.post(url, data=json.dumps(payload), headers=headers)

    subId = subId.json()
    subId = subId['id']

    return subId;


def WaitForEvents(authToken, subId, appId, policyId):
    global odlsBaseUrl
    # This calls a subscription url as a streaming http interface.
    # It is waiting for an event message.
    # The event message is sent one line at a time across the stream.
    # Each line begins with a '<linetype>:'.  
    # <linetype> may be 'event:' which indicates a new event, after the : is the type of event
    # <linetype> may be 'data:' which indicates json data, this will be data associated with
    #            the preceding event
    # An empty line is sent at the end of sending an event.
    # A typical event may look like (ignore the # at start...that is python comment):
    #     event: unmanagedEndPoint
    #     data: <some JSON data>
    #     data: <some more JSON data>
    #
    # This function receives each line until an empty line is sent.
    # This function collects all lines that begin with 'data:' and puts them in a buffer
    # This function parses the buffer as json
    # This function then parses the json to validate that the event was for an
    # 'unmanaged endpoint', and then gathers the event identifier 
    #  from the event.
    # RETURNS: nothing

    #Create a policy to allow all traffic

    url = odlsBaseUrl + '/httpsse/' + subId
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}

    while (1):
          r = requests.get(url, headers=headers, stream=True)
          jsonBuffer=''
          for line in r.iter_lines(chunk_size=1):
              if (len(line) <= 0):
                  #empty line indicates end of an event report.
                  break
              if line:
                  #json lines begin with 'data:' <json>
                  split = line.split(":",1)
                  if split[0] in ("data"):
                     print "......receiving json data from controller..."
                     jsonBuffer=jsonBuffer+split[1]
                  elif split[0] in ("event"):
                     if split[1] in (" keepAlive"):
                         print "...heart beat event received from controller..."
                     elif split[1] in (" unmanagedEndPoint"):
                         print "...receiving an unmanaged endpoint event from controller"
          # if we received 'data:' lines           
          if (len(jsonBuffer) > 0):
            print "...data event received from controller."
            r = json.loads(jsonBuffer)
            print json.dumps(r)
            eventId = r['id']
            typeInfo = r['type']
            print "...type is: " + typeInfo

            if  typeInfo in ("unmanagedEndPoint"):
                data = r['data']
                endpointId = data['id']
                endpointMac = data['mac']
                print "...unmanaged endpoint event received for mac: " + endpointMac
                print "...waiting for 5 seconds, endpoint is blocked..."
                time.sleep(5)
                print '...setting policy for unmanaged endpoint so it will be unblocked.'
                SetPolicyOnEvent(authToken, appId, eventId, policyId)


def SetPolicyOnEvent(authToken,appId,eventId,policyId):
    global odlsBaseUrl
    # This calls the OpenNAC api to connect a policy to an unmanaged endpoint
    # RETURNS: <nothing>
    url = odlsBaseUrl + '/applications/' + appId + '/requests/' + eventId
    payload = {'policy': policyId }
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    r = requests.post(url, data=json.dumps(payload), headers=headers)


def DeleteApp(authToken, appId):
    global odlsBaseUrl
    # This calls the  api to delete an application
    # RETURNS: app identifier
    url = odlsBaseUrl + '/applications/' + appId
    headers = {'content-type': 'application/json',
               'Authorization': 'bearer ' + authToken}
    r = requests.delete(url, headers=headers)


def GetCommandLineParser():
    # This method will process the command line parameters
    parser = argparse.ArgumentParser(description='Simple SDN Application to block/unblock devices connected to switch.')
    parser.add_argument('--id',required=True,
        help='your Application id.  Go to sdn-developer.elbrys.com, logon, look for SDN App ID in SDN Applications table.')
    parser.add_argument('--secret',required=True,
        help='your Application secret. Go to sdn-developer.elbrys.com, logon, look for SDN App Secret in SDN Applications table, select the "eyeball" icon next to password.')
    parser.add_argument('--switch',required=True,
        help='the Datapath Id (DPID) for the switch without ":" e.g.  ccfa00b07b95  Go to sdn-developer.elbrys.com, logon, look in "Devices" table')
    parser.add_argument('--server',required=True,
        help='The IP address.  Go to sdn-developer.elbrys.com, logon, look at "Controller" table for IP Address.')
    parser.add_argument('--port',required=True,
        help='The TCP port number for REST API.  Go to sdn-developer.elbrys.com, logon, look at "Controller" table for REST API Port.')
    return parser
 
def main(): 
    global odlsBaseUrl
    # The version of the application
    # 1.0 - initial version
    # 1.1 - added code to remove apps for selected vnet before creating new app
    version="1.1"
    print "App2"
    print "Version: " + version
    print "An application to demonstrate use of subscription."
    print __doc__

    # --------------------------------
    #    Command Line Processing
    parser=GetCommandLineParser()
    args = parser.parse_args()  
      
    odlsBaseUrl = "http://"+args.server+":"+args.port+"/ape/v1"
    print "API is at: " + odlsBaseUrl

    # --------------------------------
    #    Main application
    print " "
    print "Obtaining authorization token..."
    authToken = GetAuthToken(args.id,args.secret,parser)
    if (authToken):
        print "...authorization token obtained:" + authToken
        print " "
        print 'Creating application...'
        appId = CreateApp(authToken, args.switch,parser)
        if (appId):
            try:
                print "...application created with id:" + appId 
                print " "
                print "Creating policy to allow all traffic..."
                policyId =CreateUnblockPolicy(authToken, appId)
                print "...policy created with id:" + policyId
                print " "
                print "Creating subscription to network access events..."
                subId = CreateSubscription(authToken, appId)

                if (subId):
                    print "...subscription created with id:" + subId
                    print "Now that an application is connected to your "
                    print " switch any traffic to/from connected user devices will be blocked until a policy is defined."
                    print " Also, you can go to sdn-developer.elbrys.com and refresh the screen "
                    print " you will see this application listed in the applications table."
                    print " "
                    print "This application has created a subscription and will be receiving events from your switch.  "
                    print "It will display those events here.  When a new 'unmanaged endpoint' event occurs it "
                    print "indicates a previously unseen device has begun to pass traffic through your switch.  "
                    print "When this occurs this application will wait for a few seconds and then set a policy for "
                    print "that endpoint that allows it to pass traffic."
                    print " "
                    print "Connect a user device (laptop, tablet, phone) to a port on your network device."
                    print "Ctrl-c to exit this application."
                    WaitForEvents(authToken, subId, appId, policyId)
            except Exception as inst:
                print " Exception detected..."
                print type(inst)     # the exception instance
                print inst.args      # arguments stored in .args
                print inst           # __str__ allows args to be printed directly
            finally:
                print "Deleting application..."
                DeleteApp(authToken, appId)
                print "...application deleted."
                print ""
                print "Now that the application is deleted you will continue to have connectivity."
                print "If you go to sdn-developer.elbrys.com and refresh the screen you will "
                print " no longer see this application listed."

 
# The BASE url where the RESTful api listens
odlsBaseUrl = "http://placeholder.for.rest.api.com";

if __name__ == "__main__": 
  main()

