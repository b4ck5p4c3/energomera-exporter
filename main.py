import serial
import time
import http.server
import socketserver
import threading
import os

port_path = os.environ['SERIAL_PORT']
http_port = int(os.environ['HTTP_PORT'])

baudrate = 300
parity = serial.PARITY_EVEN
bytesize = 7
stopbits = 1

params = [
	{ 'function': 'POWPP', 'name': 'active_power', 'sub_names': ['a', 'b', 'c'], 'comment': 'Active power on phase {{phase}}' },
	{ 'function': 'VOLTA', 'name': 'voltage', 'sub_names': ['a', 'b', 'c'], 'comment': 'Voltage on phase {{phase}}' },
	{ 'function': 'CORUU', 'name': 'phase_angle', 'sub_names': ['ab', 'bc', 'ca'], 'comment': 'Phase angle between {{phase}}' },
	{ 'function': 'CURRE', 'name': 'current', 'sub_names': ['a', 'b', 'c'], 'comment': 'Current on phase {{phase}}' },
	{ 'function': 'FREQU', 'name': 'frequency', 'sub_names': ['total'], 'comment': 'Power grid frequency' },
	{ 'function': 'COS_f', 'name': 'active_power_coeff', 'sub_names': ['total', 'a', 'b', 'c'], 'comment': 'Active power coefficient on {{phase}}' },
	{ 'function': 'CORIU', 'name': 'i_u_angle', 'sub_names': ['a', 'b', 'c'], 'comment': 'Angle between I and U on phase {{phase}}' },
	{ 'function': 'TAN_f', 'name': 'reactive_power_coeff', 'sub_names': ['total', 'a', 'b', 'c'], 'comment': 'Reactive power coefficient on {{phase}}' }
]

def port_write(port, data):
	port.write(data)

def port_read(port, size):
	result = port.read(size)
	return result

def open_comms():
	port = serial.Serial(port_path, baudrate=baudrate, parity=parity, bytesize=bytesize, stopbits=stopbits, timeout=2)
	try:
 		port.close()
	except:
		pass
	port.open()
	return port

def start_comms(port):
	port_write(port, b'/?!\r\n')
	start_char = port_read(port, 1)
	print(start_char)
	if start_char != b'/':
		raise ValueError('wrong starting char')
	xxx = port_read(port, 3)
	z = port_read(port, 1)
	result = bytes()
	while len(result) < 2 or result[-2:] != b'\r\n':
		result += port_read(port, 1)
	real_name = result[:-2].decode()
	print(f'Connected to {real_name}')

def calc_bcc(data):
	result = 0
	for b in data:
		result += b
	return result & 0x7F

def check_bcc(data, bcc):
	if calc_bcc(data) != bcc:
		raise ValueError('wrong bcc')

def start_prog_mode(port):
	port_write(port, b'\x06001\r\n')
	if port_read(port, 5) != b'\x01P0\x02(':
		raise ValueError('wrong prefix')
	result = bytes()
	while len(result) < 1 or result[-1:] != b')':
		result += port_read(port, 1)
	real_id = result[:-1].decode()
	print(f'Serial number is {real_id}')
	if port_read(port, 1) != b'\x03':
		raise ValueError('wrong suffix')
	bcc = port_read(port, 1)
	check_bcc(b'P0\x02(' + result + b'\x03', bcc[0])

def authorize(port, password):
	data = b'\x01P1\x02(' + password.encode() + b')\x03'
	data += bytes([calc_bcc(data[1:])])
	port_write(port, data)
	if port_read(port, 1) != b'\x06':
		raise ValueError('failed to authorize')
	print('Authorized')

def read_params(port):
	results = {}
	for param in params:
		function = param['function']
		print(f'Reading {function}')
		data = b'\x01R1\x02' + function.encode() + b'()\x03'
		data += bytes([calc_bcc(data[1:])])
		port_write(port, data)
		raw_result = bytes()
		if port_read(port, 1) != b'\x02':
			raise ValueError('wrong header')
		while len(raw_result) < 1 or raw_result[-1:] != b'\x03':
			raw_result += port_read(port, 1)
		bcc = port_read(port, 1)
		check_bcc(raw_result, bcc[0])
		parsed_result = raw_result[:-1].decode().split('\r\n')[:-1]
		for i in range(len(param['sub_names'])):
			value = parsed_result[i].split('(')[1].split(')')[0]
			results['energomera_' + param['name'] + '{phase="' + param['sub_names'][i] + '"}'] = (param['comment'].replace('{{phase}}', param['sub_names'][i].upper()), 'gauge', value)
	return results

result_metrics = {}

last_update = time.time()

def main_query_thread():
	global result_metrics
	global last_update
	while True:
		try:
			port = open_comms()
			print('Port opened')
			start_comms(port)
			start_prog_mode(port)
			authorize(port, '777777')
			result_metrics = read_params(port)
			last_update = time.time()
			port.close()
		except Exception as e:
			print(e)
		time.sleep(10)

class MetricsHandler(http.server.BaseHTTPRequestHandler):
	def do_HEAD(self):
		self.send_response(200)
		self.send_header("content-type", "text/plain")
		self.end_headers()

	def do_GET(self):
		global result_metrics
		global last_update
		self.send_response(200)
		self.send_header("content-type", "text/plain")
		self.end_headers()
		self.wfile.write(b'# energomera metrics\n')
		metrics = result_metrics
		self.wfile.write(b'# HELP Time since last update\n')
		self.wfile.write(b'# TYPE gauge\n')
		self.wfile.write(b'energomera_time_since_last_update ' + "{:.3f}".format(time.time() - last_update).encode() + b'\n')
		for metric in metrics:
			self.wfile.write(b'# HELP ' + metrics[metric][0].encode() + b'\n')
			self.wfile.write(b'# TYPE ' + metrics[metric][1].encode() + b'\n')
			self.wfile.write(metric.encode() + b' ' + metrics[metric][2].encode() + b'\n')

def main_http_thread():
	with http.server.HTTPServer(("0.0.0.0", http_port), MetricsHandler) as server:
		print(f'Started HTTP server on {http_port}')
		server.serve_forever()


http_thread = threading.Thread(target=main_http_thread)
query_thread = threading.Thread(target=main_query_thread)

http_thread.start()
query_thread.start()

query_thread.join()
