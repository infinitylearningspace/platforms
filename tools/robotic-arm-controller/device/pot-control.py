# Simple MicroPython Robotic Arm Controller
# Potentiometer control with PWM or PCA9685 servo drivers
# No WiFi/WebSocket - Pure hardware control

import gc
from machine import Pin, PWM
import time

# Force early garbage collection
gc.collect()

# Platform detection
try:
    from machine import unique_id
    IS_ESP32 = len(unique_id()) == 6
except:
    IS_ESP32 = False

# Feature switches
USE_PCA9685 = False  # Set to True to use PCA9685 servo driver
USE_POTENTIOMETERS = True  # Set to True to enable potentiometer control

# Conditional imports - ONLY import what we need
if USE_PCA9685:
    from machine import I2C
    # PCA9685 configuration
    PCA9685_ADDRESS = 0x40
    PCA9685_FREQUENCY = 50
    if IS_ESP32:
        I2C_SCL, I2C_SDA = 22, 21
    else:
        I2C_SCL, I2C_SDA = 5, 4
    SERVO_CHANNELS = [0, 1, 2, 3]  # Which PCA9685 channels to use
else:
    # Direct PWM configuration
    if IS_ESP32:
        SERVO_PINS = [2, 4, 5, 18]
    else:   
        SERVO_PINS = [14, 12, 13, 15]

# Potentiometer configuration
if USE_POTENTIOMETERS:
    from machine import ADC
    # ADC pins for potentiometers
    if IS_ESP32:
        POT_PINS = [36, 39, 34, 35]  # ADC1 pins
    else:
        POT_PINS = [0]  # ESP8266 has only 1 ADC pin (A0)
    
    POT_DEADBAND = 3      # Deadband to prevent jitter (degrees)
    POT_READ_DELAY = 100  # Milliseconds between pot readings

# Force cleanup after imports
gc.collect()

# PCA9685 driver class (only if needed)
if USE_PCA9685:
    class PCA9685:
        def __init__(self, i2c, addr=0x40, freq=50):
            self.i2c = i2c
            self.addr = addr
            try:
                # Reset
                self.i2c.writeto_mem(addr, 0x00, b'\x00')
                
                # Calculate and set frequency
                prescale = int(25000000.0 / (4096 * freq) - 1)
                old_mode = self.i2c.readfrom_mem(addr, 0x00, 1)[0]
                
                # Go to sleep mode to set prescale
                self.i2c.writeto_mem(addr, 0x00, bytes([(old_mode & 0x7F) | 0x10]))
                self.i2c.writeto_mem(addr, 0xFE, bytes([prescale]))
                self.i2c.writeto_mem(addr, 0x00, bytes([old_mode]))
                
                # Wait for oscillator
                time.sleep_ms(5)
                
                # Enable auto increment
                self.i2c.writeto_mem(addr, 0x00, bytes([old_mode | 0xa1]))
                print("PCA9685 initialized successfully")
                
            except Exception as e:
                print(f"PCA9685 initialization error: {e}")
        
        def set_pwm(self, channel, on, off):
            """Set PWM for a specific channel"""
            try:
                reg = 0x06 + 4 * channel  # LED0_ON_L + 4 * channel
                data = bytes([
                    on & 0xFF,      # ON_L
                    on >> 8,        # ON_H
                    off & 0xFF,     # OFF_L
                    off >> 8        # OFF_H
                ])
                self.i2c.writeto_mem(self.addr, reg, data)
            except Exception as e:
                print(f"PCA9685 PWM write error: {e}")

# Potentiometer reader class (only if needed)
if USE_POTENTIOMETERS:
    class PotReader:
        def __init__(self, pins):
            self.adcs = []
            self.last_angles = []
            self.last_read = 0
            
            for pin in pins:
                try:
                    if IS_ESP32:
                        adc = ADC(Pin(pin))
                        adc.atten(ADC.ATTN_11DB)  # 0-3.3V range
                    else:
                        adc = ADC(0)  # ESP8266 only has ADC(0)
                    self.adcs.append(adc)
                    self.last_angles.append(90)  # Start at center
                except Exception as e:
                    print(f"ADC setup error for pin {pin}: {e}")
            
            print(f"Potentiometers initialized: {len(self.adcs)}")
        
        def read_pots(self):
            """Read potentiometers and return angle changes"""
            current_time = time.ticks_ms()
            
            # Rate limiting
            if time.ticks_diff(current_time, self.last_read) < POT_READ_DELAY:
                return []
            
            self.last_read = current_time
            changes = []
            
            for i, adc in enumerate(self.adcs):
                try:
                    # Read ADC value
                    raw = adc.read()
                    
                    # Convert to angle (0-180 degrees)
                    if IS_ESP32:
                        # ESP32: 12-bit ADC (0-4095)
                        angle = int((raw / 4095.0) * 180)
                    else:
                        # ESP8266: 10-bit ADC (0-1024)
                        angle = int((raw / 1024.0) * 180)
                    
                    # Apply deadband to prevent jitter
                    if abs(angle - self.last_angles[i]) > POT_DEADBAND:
                        changes.append((i, angle))
                        self.last_angles[i] = angle
                        
                except Exception as e:
                    print(f"ADC{i} read error: {e}")
            
            return changes

# Servo classes (different for PCA9685 vs PWM)
if USE_PCA9685:
    class Servo:
        def __init__(self, channel, pca, min_us=500, max_us=2500):
            self.pca = pca
            self.ch = channel
            self.a = 90
            self.min_us = min_us
            self.max_us = max_us
            self.move(90)
            print(f"Servo on PCA9685 channel {channel} initialized")
        
        def move(self, angle):
            self.a = max(0, min(180, angle))
            
            # Convert angle to pulse width in microseconds
            pulse_us = self.min_us + (self.max_us - self.min_us) * self.a / 180
            
            # Convert to 12-bit value (4096 levels at 50Hz)
            # 20ms period = 20000us
            pulse_counts = int(pulse_us / (20000 / 4096))
            
            # Set PWM (ON=0, OFF=pulse_counts)
            self.pca.set_pwm(self.ch, 0, pulse_counts)
else:
    class Servo:
        def __init__(self, pin):
            self.p = PWM(Pin(pin))
            self.p.freq(50)
            self.a = 90
            self.move(90)
            print(f"Servo on GPIO pin {pin} initialized")
        
        def move(self, angle):
            self.a = max(0, min(180, angle))
            # Convert angle to duty cycle (500-2400us pulse width)
            duty = int((500 + self.a * 11.11) * 1024 / 20000)
            self.p.duty(duty)

# Main controller class
class ArmController:
    def __init__(self):
        print("ARM Controller - Simple Version")
        print(f"Platform: {'ESP32' if IS_ESP32 else 'ESP8266'}")
        print(f"Free memory: {gc.mem_free()} bytes")
        
        # Initialize servo controller
        if USE_PCA9685:
            self.pca = self.init_pca9685()
        else:
            self.pca = None
        
        # Initialize servos
        self.init_servos()
        
        # Initialize potentiometers
        if USE_POTENTIOMETERS:
            self.init_potentiometers()
        else:
            self.pot_reader = None
        
        # Force garbage collection
        gc.collect()
        print(f"Initialization complete. Free memory: {gc.mem_free()} bytes")
    
    def init_pca9685(self):
        """Initialize PCA9685 controller"""
        if not USE_PCA9685:
            return None
            
        try:
            # Initialize I2C
            i2c = I2C(0, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=100000)
            
            # Scan for devices
            devices = i2c.scan()
            print(f"I2C devices found: {[hex(d) for d in devices]}")
            
            if PCA9685_ADDRESS not in devices:
                print(f"PCA9685 not found at address 0x{PCA9685_ADDRESS:02x}")
                return None
            
            # Initialize PCA9685
            pca = PCA9685(i2c, PCA9685_ADDRESS, PCA9685_FREQUENCY)
            return pca
            
        except Exception as e:
            print(f"PCA9685 initialization error: {e}")
            return None
    
    def init_servos(self):
        """Initialize servo objects"""
        self.servos = []
        
        if USE_PCA9685 and self.pca:
            # PCA9685 mode
            servo_count = len(SERVO_CHANNELS)
            for channel in SERVO_CHANNELS:
                servo = Servo(channel, self.pca)
                self.servos.append(servo)
        else:
            # Direct PWM mode
            servo_count = len(SERVO_PINS)
            for pin in SERVO_PINS:
                servo = Servo(pin)
                self.servos.append(servo)
        
        print(f"Servos initialized: {len(self.servos)}")
    
    def init_potentiometers(self):
        """Initialize potentiometer readers"""
        if not USE_POTENTIOMETERS:
            self.pot_reader = None
            return
            
        try:
            # Limit pot count to servo count
            servo_count = len(self.servos)
            pot_pins = POT_PINS[:servo_count]
            
            if len(pot_pins) == 0:
                print("No potentiometer pins configured")
                self.pot_reader = None
                return
            
            self.pot_reader = PotReader(pot_pins)
            
        except Exception as e:
            print(f"Potentiometer initialization error: {e}")
            self.pot_reader = None
    
    def home_servos(self):
        """Move all servos to home position (90 degrees)"""
        print("Homing all servos...")
        for i, servo in enumerate(self.servos):
            servo.move(90)
            print(f"Servo {i}: 90°")
        time.sleep(1)
    
    def demo_sweep(self):
        """Demonstration servo sweep"""
        print("Running servo sweep demo...")
        
        # Sweep each servo individually
        for servo_idx, servo in enumerate(self.servos):
            print(f"Testing servo {servo_idx}")
            
            # Sweep from 0 to 180
            for angle in range(0, 181, 30):
                servo.move(angle)
                print(f"  Servo {servo_idx}: {angle}°")
                time.sleep(0.5)
            
            # Return to center
            servo.move(90)
            time.sleep(0.5)
    
    def run(self):
        """Main control loop"""
        print("\n" + "="*40)
        print("ARM CONTROLLER READY")
        print("="*40)
        
        if USE_PCA9685:
            print(f"Mode: PCA9685 (Address: 0x{PCA9685_ADDRESS:02x})")
            print(f"Channels: {SERVO_CHANNELS}")
        else:
            print("Mode: Direct PWM")
            print(f"GPIO Pins: {SERVO_PINS}")
        
        if USE_POTENTIOMETERS and self.pot_reader:
            print(f"Potentiometers: {len(self.pot_reader.adcs)} active")
            print("Manual control enabled")
        else:
            print("Manual control disabled")
        
        print("="*40)
        
        # Home all servos
        self.home_servos()
        
        # Run demo if no potentiometers
        if not (USE_POTENTIOMETERS and self.pot_reader):
            print("No potentiometers - running demo mode")
            while True:
                try:
                    self.demo_sweep()
                    time.sleep(2)
                except KeyboardInterrupt:
                    print("\nDemo stopped by user")
                    break
                except Exception as e:
                    print(f"Demo error: {e}")
                    time.sleep(1)
        else:
            # Potentiometer control loop
            print("Potentiometer control active - move pots to control servos")
            last_status_time = 0
            
            while True:
                try:
                    # Read potentiometers
                    changes = self.pot_reader.read_pots()
                    
                    # Apply changes to servos
                    for servo_idx, angle in changes:
                        if servo_idx < len(self.servos):
                            self.servos[servo_idx].move(angle)
                            print(f"Servo {servo_idx}: {angle}°")
                    
                    # Periodic status update
                    current_time = time.ticks_ms()
                    if time.ticks_diff(current_time, last_status_time) > 5000:  # Every 5 seconds
                        angles = [s.a for s in self.servos]
                        print(f"Status - Angles: {angles}, Memory: {gc.mem_free()}")
                        last_status_time = current_time
                    
                    # Memory management
                    if gc.mem_free() < 5000:
                        gc.collect()
                    
                    # Small delay
                    time.sleep(0.02)
                    
                except KeyboardInterrupt:
                    print("\nController stopped by user")
                    break
                except Exception as e:
                    print(f"Control loop error: {e}")
                    time.sleep(0.5)
        
        # Cleanup
        print("Shutting down...")
        self.home_servos()
        print("Shutdown complete")

# Entry point
def main():
    gc.collect()
    print(f"Starting ARM Controller - Free memory: {gc.mem_free()}")
    
    try:
        controller = ArmController()
        controller.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        # Try to home servos on error
        try:
            print("Attempting emergency servo home...")
            time.sleep(1)
        except:
            pass

if __name__ == "__main__":
    main()