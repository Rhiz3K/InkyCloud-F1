from app.services.renderer import Renderer


def verify():
    # Mock translator
    translator = {}
    r = Renderer(translator)

    # Check layout constants
    results_col1_x = r.layout["results_col1_x"]
    results_col2_x = r.layout["results_col2_x"]

    print(f"results_col1_x: {results_col1_x}")
    print(f"results_col2_x: {results_col2_x}")

    # Calculate gap for time ending at (154 + 240 + 61)
    # Time print X start = results_col1_x + offset
    offset = r.layout.get("results_time_offset", 230)
    time_x_start = results_col1_x + offset
    # Time width approx 61
    time_x_end = time_x_start + 61

    _ = results_col2_x - time_x_end  # gap (unused but calculated for verification)

    print(f"Time starts at: {time_x_start}")
    print(f"Time ends approx at: {time_x_end}")

    # Expected values
    expected_col1 = 114
    expected_offset = 260

    if results_col1_x == expected_col1:
        print(f"SUCCESS: results_col1_x is {expected_col1}")
    else:
        print(f"FAILURE: results_col1_x is {results_col1_x}, expected {expected_col1}")

    # Check offset
    if r.layout.get("results_time_offset") == expected_offset:
        print(f"SUCCESS: results_time_offset is {expected_offset}")
    else:
        print("FAILURE: results_time_offset mismatch")


if __name__ == "__main__":
    verify()
