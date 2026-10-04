local common = {
    hold_bottom_left_corner = { toggle_tailscale_vpn = true },
    multiswipe_east_north = { history = true },
    multiswipe_east_south = { go_to = true },
    multiswipe_east_south_west_north = { full_refresh = true },
    multiswipe_east_west_east = { favorites = true },
    multiswipe_northwest_southwest_northwest = { toggle_wifi = true },
    multiswipe_south_west = { show_frontlight_dialog = true },
    multiswipe_southeast_northeast_northwest = { wifi_on = true },
    multiswipe_southeast_southwest_northwest = { wifi_off = true },
    multiswipe_west_east_west = { open_previous_document = true },
    multiswipe_west_south = { back = true },
    one_finger_swipe_left_edge_down = { decrease_frontlight = 0 },
    one_finger_swipe_left_edge_up = { increase_frontlight = 0 },
    one_finger_swipe_right_edge_down = { decrease_frontlight_warmth = 0 },
    one_finger_swipe_right_edge_up = { increase_frontlight_warmth = 0 },
    short_diagonal_swipe = { full_refresh = true },
    tap_left_bottom_corner = { toggle_frontlight = true },
    two_finger_swipe_north = { increase_frontlight = 0 },
    two_finger_swipe_south = { decrease_frontlight = 0 },
}
local reader = {
    double_tap_left_side = { page_jmp = -10 },
    double_tap_right_side = { page_jmp = 10 },
    hold_bottom_right_corner = { bookorbit_sync_now = true },
    multiswipe_east_north_west = { zoom = "contentwidth" },
    multiswipe_east_north_west_east = { zoom = "pagewidth" },
    multiswipe_east_west = { latest_bookmark = true },
    multiswipe_north_east = { toc = true },
    multiswipe_north_south_north = { prev_chapter = true },
    multiswipe_north_west = { bookmarks = true },
    multiswipe_south_east = { toggle_reflow = true },
    multiswipe_south_east_north = { zoom = "contentheight" },
    multiswipe_south_east_north_south = { zoom = "pageheight" },
    multiswipe_south_north = { skim = true },
    multiswipe_south_north_south = { next_chapter = true },
    multiswipe_southeast_northeast = { follow_nearest_link = true },
    multiswipe_west_east = { previous_location = true },
    multiswipe_west_north = { book_status = true },
    pinch_gesture = { decrease_font = 0 }, spread_gesture = { increase_font = 0 },
    tap_top_left_corner = { toggle_page_flipping = true },
    tap_top_right_corner = { toggle_bookmark = true },
    two_finger_swipe_east = { toc = true }, two_finger_swipe_west = { bookmarks = true },
}
local fm = {
    hold_top_right_corner = { refresh_content = true },
    multiswipe_north_south = { folder_up = true },
    multiswipe_north_west = { folder_shortcuts = true },
    two_finger_swipe_west = { folder_shortcuts = true },
}
for key, value in pairs(common) do
    fm[key], reader[key] = value, value
end
return {
    set = { gesture_fm = fm, gesture_reader = reader },
    remove = {
        { "gesture_fm", "hold_bottom_left_corner", "toggle_tailscale_network" },
        { "gesture_reader", "hold_bottom_left_corner", "toggle_tailscale_network" },
    },
}
