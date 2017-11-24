#!/usr/bin/env python3

import os, subprocess, yaml, sys
import threading
from queue import Queue
import requests
import time
from jinja2 import Template
from urllib.parse import urlencode, quote_plus
import syslog

class Praesto:
    config = {}
    cache = {}
    def __init__(self):
        self.read_config()
        self.log("Starting Praesto",'info')
        print_lock = threading.Lock()
        self.queue = Queue()

        os.makedirs(self.config['state_dir'],exist_ok=True)

    def log(self,msg,level='error'):
        syslog.openlog(ident=self.config['log_identity'])
        if level == 'debug' and self.config['debug_log']:
            syslog.syslog(syslog.LOG_DEBUG,msg)

        if level == 'info':
            syslog.syslog(syslog.LOG_INFO,msg)

        if level == 'error':
            syslog.syslog(syslog.LOG_ERR,msg)

    def run(self):
        for i in range(self.config['threads']):
            t = threading.Thread(target=self.process_queue)
            t.daemon = True
            t.start()
            start = time.time()
            for check in self.config['checks']:
                self.queue.put(check)
            self.queue.join()
            run_time = time.time() - start
            return run_time, threading.enumerate()

    # 0
    def process_queue(self):
        while True:
            check = self.queue.get()
            check['changed'] = False
            self.log("Checking host %s" % check['destination'], 'debug')
            if check['type'] == "ping" and check['enabled']:
                check = self.check_ping(check)
            if check['changed']:
                self.set_state(check)
            if check['changed'] and check['iterator'] == 0:
                self.send_notifications(check)
            self.queue.task_done()
    
    # 3
    def set_state(self,check):
        history = { 'timestamp': time.time(), 'state': check['state'] }
        check['history'].append(history)
        p = "%s/%s.state" % (self.config['state_dir'],check['id']) 
        self.write_yaml(p,check)

    # 2
    def get_state(self,check):
        p = "%s/%s.state" % (self.config['state_dir'],check['id']) 
        if not os.path.exists(p):
            state = {}
            state['last_state'] = None
            state['state'] = "UNKNOWN"
            state['history'] = []
            state['id'] = check['id']
            state['iterator'] = 0
            return state
        return self.read_yaml(p)
 
    # 1
    def check_ping(self,check):
        state = self.get_state(check)
        check.update(state)
        response = os.system("ping -c 1 %s 1> /dev/null" % check['destination']) 
        check['changed'] = False
        self.log("Checked host %s: %s" % (check['destination'],response),'debug')
        if response != check['last_state'] and check['iterator'] < check['threshold']:
            self.log("Changing state host %s to %s (%/%)" % (check['destination'],'PENDING',check['iterator'],check['threshold']))
            check['iterator'] += 1
            check['changed'] = True
            check['state'] = "PENDING"
        if (response != check['last_state'] and check['iterator'] == check['threshold'] or
            response == check['last_state'] and check['iterator']):
            check['changed'] = True
            check['last_state'] = response
            check['iterator'] = 0
            if response:
                self.log("Changing state host %s to %s (%s/%s)" % (check['destination'],'UNREACHABLE',check['iterator'],check['threshold']))
                check['state'] = "UNREACHABLE"
            else:
                self.log("Changing state host %s to %s (%s/%s)" % (check['destination'],'REACHABLE',check['iterator'],check['threshold']))
                check['state'] = "REACHABLE"
        return check

    def send_notifications(self,check):
        if 'notify' not in check:
            return True

        template = Template("Host: {{ check.destination }}\nDescription: {{ check.description }}\nType: {{ check.type }}\nState: {{ check.state }}")

        message = template.render(check=check)
        
        for n in check['notify']:
            notify = self.config['notifications'][n]
            if notify['type'] == "telegram":
                self.notify_telegram(notify['telegram_token'], notify['telegram_chat_id'],message)
            if notify['type'] == "cheapconnect":
                self.notify_cheapconnect(notify['cc_token'],notify['sender'],notify['recipient'],message)

    def notify_telegram(self,token,chat_id,message):
        telegram_url = "https://api.telegram.org/bot%s/sendMessage" % token
        params = { 'chat_id': chat_id, 'disable_web_page_preview': 1, 'text': message }
        r = requests.get(telegram_url, data=params)
        if r.status_code != 200:
            self.log(r.text,'info')
        else:
            self.log('Notification sent to %s' % chat_id,'info')

    def notify_cheapconnect(self,token,sender,recipient,message):
        message = quote_plus(message)
        cheapconnect_url = "https://account.cheapconnect.net/API/v1/sms/SendSMS/%s" % token
        cheapconnect_url = "%s/%s/%s/%s" % (cheapconnect_url,sender,recipient,message)
        r = requests.get(cheapconnect_url)
        if r.status_code != 200:
            self.log(r.text,'info')
        else:
            self.log('Notification sent to %s' % (recipient),'info')

    def read_yaml(self,p):
        with open(p,'r') as fh:
            try:
                return yaml.load(fh)
            except yaml.YAMLError as exc:
                print(exc)
                self.log(exc)

    def write_yaml(self,p, c):
        with open(p,'w') as fh:
            try:
                fh.write(yaml.dump(c, indent=4, default_flow_style=False))
            except Exception as exc:
                print(exc)
                self.log(exc)
    
    def read_config(self,p='config/config.yaml'):
        self.config = self.read_yaml(p)

    def write_config(self,p='config/config.yaml'):
        return self.write_yaml(p,self.config)

if __name__ == "__main__":
    praesto = Praesto()
    while True:
        praesto.read_config()
        run_time,ex = praesto.run()
        praesto.log("Finished a check run in %s" % (run_time))
        praesto.log("Sleeping for %s" % (praesto.config['check_interval']))
        time.sleep(praesto.config['check_interval'])
