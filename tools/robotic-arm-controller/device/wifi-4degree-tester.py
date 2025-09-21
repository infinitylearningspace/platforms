# Ultra-compact MicroPython Robotic Arm Controller
# HTTP-only, WiFi client mode, with recording/playback features

import asyncio
import json
import gc
from machine import Pin, PWM
import network

# Platform detection
try:
    from machine import unique_id
    IS_ESP32 = len(unique_id()) == 6
except:
    IS_ESP32 = False

# Minimal servo class
class Servo:
    def __init__(self, pin):
        self.p = PWM(Pin(pin))
        self.p.freq(50)
        self.a = 90
        self.move(90)
    
    def move(self, angle):
        self.a = max(0, min(180, angle))
        #d = int((500 + self.a * 11.11) * 1024 / 20000)
        # Alternative more precise calculation:
        pulse_width = 1000 + (self.a /180) * 1000  # microseconds
        d = int(pulse_width * 1024 / 20000)   # convert to duty cycle
        self.p.duty(d)

# Main controller
class ArmCtrl:
    def __init__(self):
        # WiFi Configuration - CHANGE THESE VALUES
        self.WIFI_SSID = "dummy"
        self.WIFI_PASSWORD = "dummy"
        
        # Hardware config
        if IS_ESP32:
            pins = [2, 4, 5, 18]
        else:
            pins = [14, 12, 13, 15]
            
        self.dof = 4
        self.servos = []
        for i in range(self.dof):
            self.servos.append(Servo(pins[i]))
        
        self.q = []
        self.stop = False
        
        # Recording and playback
        self.is_playing = False
        self.playback_queue = []
        self.current_recording = None
        
        # Status LED
        try:
            self.led = Pin(2, Pin.OUT)
        except:
            self.led = None
            
        gc.collect()
    
    async def wifi_setup(self):
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        
        if sta.isconnected():
            ip = sta.ifconfig()[0]
            print("Already connected: " + ip)
            return True
        
        print("Connecting to " + self.WIFI_SSID + "...")
        sta.connect(self.WIFI_SSID, self.WIFI_PASSWORD)
        
        timeout = 20
        while not sta.isconnected() and timeout > 0:
            await asyncio.sleep(1)
            timeout -= 1
            print(".", end="")
        
        if sta.isconnected():
            ip = sta.ifconfig()[0]
            print("")
            print("Connected! IP: " + ip)
            print("Use this IP in web app: " + ip)
            return True
        else:
            print("")
            print("Failed to connect to " + self.WIFI_SSID)
            print("Check WIFI_SSID and WIFI_PASSWORD")
            return False
    
    async def handle_req(self, r, w):
        try:
            # Read request data
            req = await r.read(1024)
            if len(req) == 0:
                return
                
            req_str = req.decode()
            lines = req_str.split('\r\n')
            if len(lines) == 0:
                return
                
            # Parse request line
            request_parts = lines[0].split(' ')
            if len(request_parts) < 2:
                return
                
            method = request_parts[0]
            path = request_parts[1]
            
            print("Request: " + method + " " + path)
            
            # CORS headers
            h = "HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\n"
            
            if method == "OPTIONS":
                w.write((h + "\r\n").encode())
                
            elif path == "/status":
                angles = []
                for servo in self.servos:
                    angles.append(servo.a)
                    
                data = {
                    "ok": True,
                    "dof": self.dof,
                    "angles": angles,
                    "emergency_stop": self.stop,
                    "is_playing": self.is_playing,
                    "recording_file": self.current_recording
                }
                json_data = json.dumps(data)
                response = h + "Content-Type: application/json\r\nContent-Length: " + str(len(json_data)) + "\r\n\r\n" + json_data
                w.write(response.encode())
                
            elif path == "/joints" and method == "POST":
                # Handle joint movement commands
                success = await self.handle_post_data(r, lines, self.process_cmd)
                if success:
                    response = h + "Content-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}"
                    w.write(response.encode())
                else:
                    error_resp = "HTTP/1.1 400 Bad Request\r\n\r\nInvalid data"
                    w.write(error_resp.encode())
                    
            elif path == "/emergency" and method == "POST":
                self.stop = True
                self.q = []
                self.is_playing = False
                self.playback_queue = []
                print("Emergency stop!")
                response = h + "Content-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}"
                w.write(response.encode())
                
            elif path == "/recording" and method == "POST":
                # Handle save recording
                success = await self.handle_post_data(r, lines, self.save_recording)
                if success:
                    response = h + "Content-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}"
                    w.write(response.encode())
                else:
                    error_resp = "HTTP/1.1 500 Internal Server Error\r\n\r\nSave failed"
                    w.write(error_resp.encode())
                    
            elif path == "/playback" and method == "POST":
                # Handle playback
                success = await self.handle_post_data(r, lines, self.start_playback)
                if success:
                    response = h + "Content-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}"
                    w.write(response.encode())
                else:
                    error_resp = "HTTP/1.1 400 Bad Request\r\n\r\nPlayback failed"
                    w.write(error_resp.encode())
                    
            else:
                error_resp = "HTTP/1.1 404 Not Found\r\n\r\nNot Found"
                w.write(error_resp.encode())
                
            await w.drain()
            
        except Exception as e:
            print("Request error: " + str(e))
        finally:
            try:
                await w.wait_closed()
            except:
                pass
    
    async def handle_post_data(self, reader, lines, handler_func):
        try:
            # Find content length and body
            cl = 0
            header_end = -1
            
            for i, line in enumerate(lines):
                if "Content-Length:" in line or "content-length:" in line.lower():
                    cl = int(line.split(":")[1].strip())
                    print("Content-Length: " + str(cl))
                if line == "":
                    header_end = i
                    break
            
            body_data = ""
            
            if header_end >= 0 and header_end + 1 < len(lines):
                remaining_lines = lines[header_end + 1:]
                body_data = "\r\n".join(remaining_lines)
                print("Body from initial read: " + body_data)
            
            # Read more if needed
            if cl > 0 and len(body_data) < cl:
                needed = cl - len(body_data)
                print("Reading " + str(needed) + " more bytes")
                additional = await reader.read(needed)
                body_data += additional.decode()
            
            print("Final body: " + body_data)
            
            if len(body_data) > 0:
                cmd = json.loads(body_data)
                print("Parsed command: " + str(cmd))
                return await handler_func(cmd)
            
            return False
            
        except Exception as e:
            print("POST data error: " + str(e))
            return False
    
    async def process_cmd(self, cmd):
        if self.stop:
            return True
            
        try:
            cmd_type = cmd.get('type')
            if cmd_type == 'joint_move':
                j = cmd.get('joint', 0)
                a = cmd.get('angle', 90)
                if 0 <= j < self.dof:
                    self.q.append((j, a))
            elif cmd_type == 'emergency_stop':
                self.stop = True
                self.q = []
            return True
        except:
            return False
    
    async def save_recording(self, cmd):
        try:
            filename = cmd.get('filename', 'recording.json')
            movements = cmd.get('movements', [])
            
            # Limit filename length
            if len(filename) > 30:
                filename = 'rec_' + str(len(movements)) + '.json'
            
            print("Saving " + str(len(movements)) + " movements to " + filename)
            
            # Save to file in simplified format
            with open(filename, 'w') as f:
                simple_data = []
                for move in movements:
                    simple_data.append([
                        move.get('joint', 0), 
                        move.get('angle', 90), 
                        move.get('delay', 500)
                    ])
                f.write(json.dumps(simple_data))
            
            print("Recording saved successfully")
            self.current_recording = filename
            return True
            
        except Exception as e:
            print("Save recording error: " + str(e))
            return False
    
    async def start_playback(self, cmd):
        try:
            movements = cmd.get('movements', [])
            if len(movements) == 0:
                print("No movements to playback")
                return False
            
            print("Starting playback of " + str(len(movements)) + " movements")
            
            # Clear current queue and set playback mode
            self.q = []
            self.is_playing = True
            
            # Add movements to playback queue
            self.playback_queue = []
            for move in movements:
                joint = move.get('joint', 0)
                angle = move.get('angle', 90)
                delay = move.get('delay', 500)
                self.playback_queue.append((joint, angle, delay))
            
            print("Playback queue prepared")
            return True
            
        except Exception as e:
            print("Start playback error: " + str(e))
            self.is_playing = False
            return False
    
    async def servo_task(self):
        while True:
            try:
                # Handle playback mode
                if self.is_playing and len(self.playback_queue) > 0:
                    joint, angle, delay = self.playback_queue.pop(0)
                    if joint < len(self.servos):
                        self.servos[joint].move(angle)
                        print("Playback: Joint " + str(joint) + " -> " + str(angle))
                        
                        # LED blink for playback
                        if self.led:
                            self.led.on()
                            await asyncio.sleep(0.05)
                            self.led.off()
                        
                        # Wait for specified delay
                        await asyncio.sleep(delay / 1000.0)
                    
                    # Check if playback finished
                    if len(self.playback_queue) == 0:
                        self.is_playing = False
                        print("Playback completed")
                
                # Handle normal command queue
                elif not self.stop and len(self.q) > 0:
                    j, a = self.q.pop(0)
                    if j < len(self.servos):
                        self.servos[j].move(a)
                        print("Joint " + str(j) + " -> " + str(a))
                        
                        # LED blink
                        if self.led:
                            self.led.on()
                            await asyncio.sleep(0.02)
                            self.led.off()
                
                # Auto-clear emergency stop
                if self.stop:
                    await asyncio.sleep(3)
                    self.stop = False
                    print("Emergency cleared")
                    
                await asyncio.sleep(0.05)
                
            except Exception as e:
                print("Servo error: " + str(e))
                await asyncio.sleep(0.1)
    
    async def mem_task(self):
        count = 0
        while True:
            try:
                # Memory management
                if gc.mem_free() < 5000:
                    gc.collect()
                    
                # Heartbeat every 10 cycles (20 seconds)
                count += 1
                if count >= 10:
                    count = 0
                    if self.led and not self.stop:
                        self.led.on()
                        await asyncio.sleep(0.05)
                        self.led.off()
                    print("Memory: " + str(gc.mem_free()) + " bytes")
                    
                await asyncio.sleep(2)
                
            except:
                await asyncio.sleep(1)
    
    async def run(self):
        print("Robotic Arm Controller")
        if IS_ESP32:
            print("Platform: ESP32")
        else:
            print("Platform: ESP8266")
        print("Memory: " + str(gc.mem_free()) + " bytes")
        
        # Setup WiFi
        if not await self.wifi_setup():
            print("WiFi required! Check credentials")
            return
        
        # Start HTTP server
        try:
            srv = await asyncio.start_server(self.handle_req, "0.0.0.0", 80)
            print("HTTP server started on port 80")
        except Exception as e:
            print("HTTP server failed: " + str(e))
            return
        
        # Start background tasks
        task1 = asyncio.create_task(self.servo_task())
        task2 = asyncio.create_task(self.mem_task())
        
        print("System ready!")
        print("DOF: " + str(self.dof))
        
        try:
            await asyncio.gather(task1, task2)
        except KeyboardInterrupt:
            print("Stopping...")
        except Exception as e:
            print("Main error: " + str(e))

# Main function
async def main():
    ctrl = ArmCtrl()
    await ctrl.run()

# Auto-start
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("Boot error: " + str(e))
        import time
        time.sleep(2)
        import machine
        machine.reset()