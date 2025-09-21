# ESP32 + PCA9685 Servo Motor Test Code
# Compatible with Thonny IDE and serial interface
# Supports 6x 180-degree servo motors

from machine import Pin, I2C
import time

class PCA9685:
    """Simple PCA9685 PWM controller driver for servo control"""
    
    def __init__(self, i2c, address=0x40, freq=50):
        self.i2c = i2c
        self.address = address
        self.freq = freq
        self.reset()
        self.set_pwm_freq(freq)
    
    def reset(self):
        """Reset the PCA9685"""
        self.i2c.writeto_mem(self.address, 0x00, bytes([0x00]))
    
    def set_pwm_freq(self, freq_hz):
        """Set PWM frequency (typically 50Hz for servos)"""
        prescaleval = 25000000.0    # 25MHz
        prescaleval /= 4096.0       # 12-bit
        prescaleval /= float(freq_hz)
        prescaleval -= 1.0
        prescale = int(prescaleval + 0.5)
        
        oldmode = self.i2c.readfrom_mem(self.address, 0x00, 1)[0]
        newmode = (oldmode & 0x7F) | 0x10    # sleep
        self.i2c.writeto_mem(self.address, 0x00, bytes([newmode]))
        self.i2c.writeto_mem(self.address, 0xFE, bytes([prescale]))
        self.i2c.writeto_mem(self.address, 0x00, bytes([oldmode]))
        time.sleep_ms(5)
        self.i2c.writeto_mem(self.address, 0x00, bytes([oldmode | 0xa1]))
    
    def set_pwm(self, channel, on, off):
        """Set PWM value for a channel"""
        self.i2c.writeto_mem(self.address, 0x06 + 4 * channel, bytes([on & 0xFF]))
        self.i2c.writeto_mem(self.address, 0x07 + 4 * channel, bytes([on >> 8]))
        self.i2c.writeto_mem(self.address, 0x08 + 4 * channel, bytes([off & 0xFF]))
        self.i2c.writeto_mem(self.address, 0x09 + 4 * channel, bytes([off >> 8]))
    
    def set_servo_angle(self, channel, angle):
        """Set servo to specific angle (0-180 degrees)"""
        # Typical servo pulse widths: 0.5ms (0°) to 2.5ms (180°)
        # For 50Hz (20ms period), this translates to duty cycles of ~2.5% to ~12.5%
        # PCA9685 has 4096 steps, so:
        # 0° = ~102 (0.5ms/20ms * 4096)
        # 180° = ~512 (2.5ms/20ms * 4096)
        
        if angle < 0:
            angle = 0
        elif angle > 180:
            angle = 180
        
        pulse = int(102 + (angle / 180.0) * (512 - 102))
        self.set_pwm(channel, 0, pulse)

class ServoTester:
    """Servo testing class with various test routines"""
    
    def __init__(self, sda_pin=21, scl_pin=22, pca_address=0x40):
        print("Initializing ESP32 + PCA9685 Servo Tester...")
        
        # Initialize I2C
        self.i2c = I2C(0, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=100000)
        
        # Scan for I2C devices
        devices = self.i2c.scan()
        print(f"I2C devices found: {[hex(device) for device in devices]}")
        
        if pca_address not in devices:
            print(f"ERROR: PCA9685 not found at address {hex(pca_address)}")
            print("Check your wiring and connections!")
            return
        
        # Initialize PCA9685
        self.pca = PCA9685(self.i2c, pca_address)
        self.servo_count = 6  # Number of servos connected
        print(f"PCA9685 initialized successfully at {hex(pca_address)}")
        print(f"Ready to test {self.servo_count} servo motors\n")
    
    def test_single_servo(self, servo_num):
        """Test a single servo with basic movements"""
        if servo_num >= self.servo_count:
            print(f"Error: Servo {servo_num} is out of range (0-{self.servo_count-1})")
            return
        
        print(f"Testing Servo {servo_num}:")
        
        # Test positions
        positions = [0, 45, 90, 135, 180, 90, 0]
        
        for angle in positions:
            print(f"  Moving to {angle}°")
            self.pca.set_servo_angle(servo_num, angle)
            time.sleep(1)
        
        print(f"Servo {servo_num} test complete\n")
    
    def test_all_servos_sequential(self):
        """Test all servos one by one"""
        print("=== SEQUENTIAL SERVO TEST ===")
        for i in range(self.servo_count):
            self.test_single_servo(i)
        print("Sequential test complete!\n")
    
    def test_all_servos_simultaneous(self):
        """Test all servos moving together"""
        print("=== SIMULTANEOUS SERVO TEST ===")
        
        positions = [0, 45, 90, 135, 180, 90, 0]
        
        for angle in positions:
            print(f"Moving all servos to {angle}°")
            for servo in range(self.servo_count):
                self.pca.set_servo_angle(servo, angle)
            time.sleep(2)
        
        print("Simultaneous test complete!\n")
    
    def test_sweep(self, servo_num=None):
        """Continuous sweep test"""
        if servo_num is None:
            print("=== SWEEP TEST (All Servos) ===")
            servos = list(range(self.servo_count))
        else:
            if servo_num >= self.servo_count:
                print(f"Error: Servo {servo_num} is out of range")
                return
            print(f"=== SWEEP TEST (Servo {servo_num}) ===")
            servos = [servo_num]
        
        print("Performing 3 complete sweeps...")
        
        for sweep in range(3):
            print(f"Sweep {sweep + 1}/3")
            
            # Forward sweep (0 to 180)
            for angle in range(0, 181, 5):
                for servo in servos:
                    self.pca.set_servo_angle(servo, angle)
                time.sleep_ms(50)
            
            # Backward sweep (180 to 0)
            for angle in range(180, -1, -5):
                for servo in servos:
                    self.pca.set_servo_angle(servo, angle)
                time.sleep_ms(50)
        
        # Return to center
        for servo in servos:
            self.pca.set_servo_angle(servo, 90)
        
        print("Sweep test complete!\n")
    
    def center_all_servos(self):
        """Center all servos to 90 degrees"""
        print("Centering all servos to 90°...")
        for servo in range(self.servo_count):
            self.pca.set_servo_angle(servo, 90)
        print("All servos centered\n")
    
    def interactive_test(self):
        """Interactive servo testing via serial"""
        print("=== INTERACTIVE SERVO TEST ===")
        print("Commands:")
        print("  's<num> <angle>' - Move servo <num> to <angle> (e.g., 's0 90')")
        print("  'center' - Center all servos")
        print("  'sweep' - Sweep all servos")
        print("  'sweep<num>' - Sweep specific servo (e.g., 'sweep0')")
        print("  'test<num>' - Test specific servo (e.g., 'test0')")
        print("  'testall' - Test all servos sequentially")
        print("  'simul' - Test all servos simultaneously")
        print("  'quit' - Exit interactive mode")
        print()
        
        while True:
            try:
                cmd = input("Enter command: ").strip().lower()
                
                if cmd == 'quit':
                    break
                elif cmd == 'center':
                    self.center_all_servos()
                elif cmd == 'sweep':
                    self.test_sweep()
                elif cmd.startswith('sweep') and len(cmd) > 5:
                    servo_num = int(cmd[5:])
                    self.test_sweep(servo_num)
                elif cmd.startswith('test') and len(cmd) > 4:
                    if cmd[4:] == 'all':
                        self.test_all_servos_sequential()
                    else:
                        servo_num = int(cmd[4:])
                        self.test_single_servo(servo_num)
                elif cmd == 'simul':
                    self.test_all_servos_simultaneous()
                elif cmd.startswith('s') and ' ' in cmd:
                    parts = cmd.split()
                    servo_num = int(parts[0][1:])
                    angle = int(parts[1])
                    if servo_num < self.servo_count and 0 <= angle <= 180:
                        print(f"Moving servo {servo_num} to {angle}°")
                        self.pca.set_servo_angle(servo_num, angle)
                    else:
                        print("Invalid servo number or angle")
                else:
                    print("Unknown command. Type 'quit' to exit.")
                    
            except (ValueError, IndexError):
                print("Invalid command format")
            except KeyboardInterrupt:
                print("\nExiting...")
                break
    
    def run_full_test_suite(self):
        """Run complete test suite"""
        print("=" * 50)
        print("ESP32 + PCA9685 SERVO TEST SUITE")
        print("=" * 50)
        
        # Center all servos first
        self.center_all_servos()
        time.sleep(2)
        
        # Test each servo individually
        self.test_all_servos_sequential()
        
        # Test all servos together
        self.test_all_servos_simultaneous()
        
        # Sweep test
        self.test_sweep()
        
        # Center and finish
        self.center_all_servos()
        
        print("=" * 50)
        print("FULL TEST SUITE COMPLETE!")
        print("All servos should now be at 90° (center position)")
        print("=" * 50)

# Main execution
def main():
    """Main function to run servo tests"""
    try:
        # Create servo tester instance
        # Adjust SDA/SCL pins if your wiring is different
        tester = ServoTester(sda_pin=21, scl_pin=22, pca_address=0x40)
        
        print("Choose test mode:")
        print("1. Full automated test suite")
        print("2. Interactive manual control")
        print("3. Quick center test")
        
        choice = input("Enter choice (1-3): ").strip()
        
        if choice == '1':
            tester.run_full_test_suite()
        elif choice == '2':
            tester.interactive_test()
        elif choice == '3':
            tester.center_all_servos()
        else:
            print("Invalid choice, running full test suite...")
            tester.run_full_test_suite()
            
    except Exception as e:
        print(f"Error: {e}")
        print("\nTroubleshooting tips:")
        print("- Check I2C wiring (SDA=GPIO21, SCL=GPIO22)")
        print("- Verify PCA9685 power supply (5V recommended)")
        print("- Check servo connections to PCA9685 channels 0-5")
        print("- Ensure PCA9685 address is 0x40 (default)")

# Auto-run if this is the main script
#if __name__ == "__main__":
main()

# Quick test functions (uncommesnt to use individually)

# Test individual servo
# tester = ServoTester()
# tester.test_single_servo(0)  # Test servo on channel 0

# Center all servos quickly
# tester = ServoTester()
# tester.center_all_servos()

# Interactive mode
# tester = ServoTester()
# tester.interactive_test()