"""
Diagnostic script to reproduce and capture detailed crash info.
Tests text measuring under concurrent conditions.
"""
import sys
import traceback
import faulthandler
import threading
import time
from pathlib import Path

# Enable faulthandler to capture segfaults
faulthandler.enable()

# Add project root to path
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

def test_font_metrics_concurrent():
    """Test QFontMetrics under concurrent/multi-threaded access."""
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication
    
    # Initialize Qt
    app = QApplication.instance() or QApplication([])
    
    print("[DIAGNOSTIC] Testing concurrent QFontMetrics access...")
    
    test_strings = [
        "Short text",
        "A much longer piece of text that might wrap",
        "VeryLongWordWithoutSpacesThatShouldTriggerCharacterLevelWrapping",
        "Mix of short and VeryLongWordWithoutSpacesThatShouldTriggerCharacterLevelWrapping text",
    ]
    
    results = []
    errors = []
    
    def worker(thread_id, num_iterations):
        """Worker thread measuring text."""
        try:
            font = QFont("Arial", 12)
            metrics = QFontMetrics(font)
            
            for i in range(num_iterations):
                for text in test_strings:
                    width = metrics.horizontalAdvance(text)
                    height = metrics.height()
                    results.append((thread_id, i, text[:20], width, height))
                    
                    # Stress test: try measuring while font changes
                    if i % 10 == 0:
                        font.setPointSize(10 + (i % 4))
                        metrics = QFontMetrics(font)
        except Exception as e:
            errors.append((thread_id, str(e), traceback.format_exc()))
    
    # Launch concurrent threads
    threads = []
    for tid in range(4):
        t = threading.Thread(target=worker, args=(tid, 50), daemon=False)
        threads.append(t)
        t.start()
    
    # Wait for all threads
    for t in threads:
        t.join(timeout=10)
    
    if errors:
        print(f"[ERROR] {len(errors)} errors occurred:")
        for tid, err, tb in errors:
            print(f"  Thread {tid}: {err}")
            print(f"  Traceback:\n{tb}")
        return False
    else:
        print(f"[SUCCESS] Measured {len(results)} texts across {len(threads)} threads")
        return True

def test_button_text_wrapping():
    """Test the actual button text wrapping under stress."""
    from PySide6.QtWidgets import QApplication, QPushButton
    from ui.widgets.sound_file_button import SoundFileButton
    
    app = QApplication.instance() or QApplication([])
    
    print("\n[DIAGNOSTIC] Testing SoundFileButton text wrapping...")
    
    errors = []
    
    def worker(thread_id, num_iterations):
        """Worker testing button text wrapping."""
        try:
            for i in range(num_iterations):
                btn = SoundFileButton(label=f"Test Button {i}")
                btn.resize(120, 120)
                
                # Test various text inputs
                texts = [
                    "Short",
                    "Medium length text here",
                    "VeryLongWordWithoutSpacesThatCanCauseProblems",
                    "Mix Short and VeryLongWordWithoutSpacesThatCanCauseProblems Together",
                ]
                
                for text in texts:
                    try:
                        wrapped = btn._auto_wrap_text(text)
                        print(f"[T{thread_id}] Wrapped '{text[:30]}...' -> {len(wrapped)} chars")
                    except Exception as e:
                        errors.append((thread_id, i, text, str(e)))
        except Exception as e:
            errors.append((thread_id, -1, "init", str(e)))
    
    # Test with multiple threads
    threads = []
    for tid in range(2):
        t = threading.Thread(target=worker, args=(tid, 10), daemon=False)
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join(timeout=15)
    
    if errors:
        print(f"\n[ERROR] {len(errors)} errors in button wrapping:")
        for tid, idx, text, err in errors:
            print(f"  Thread {tid} iteration {idx} text '{text}': {err}")
        return False
    else:
        print("[SUCCESS] All button text wrapping tests passed")
        return True

def main():
    """Run diagnostic suite."""
    print("=" * 70)
    print("CRASH DIAGNOSTIC SUITE")
    print("=" * 70)
    
    results = {}
    
    try:
        print("\n[1/2] Testing concurrent QFontMetrics...")
        results["font_metrics"] = test_font_metrics_concurrent()
    except Exception as e:
        print(f"[CRASH] QFontMetrics test crashed: {e}")
        print(traceback.format_exc())
        results["font_metrics"] = False
    
    try:
        print("\n[2/2] Testing button text wrapping...")
        results["button_wrapping"] = test_button_text_wrapping()
    except Exception as e:
        print(f"[CRASH] Button wrapping test crashed: {e}")
        print(traceback.format_exc())
        results["button_wrapping"] = False
    
    print("\n" + "=" * 70)
    print("DIAGNOSTIC RESULTS:")
    for test, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {test}: {status}")
    print("=" * 70)
    
    return all(results.values())

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
