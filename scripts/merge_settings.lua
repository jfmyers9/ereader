-- Run by settings.py with host LuaJIT. No device code or libraries are loaded.
jit.off()

local function read_table(path, missing_ok, is_profile)
    local file = io.open(path, "rb")
    if not file then
        if missing_ok then return {} end
        error("Cannot read settings/profile file")
    end
    local source = file:read(2097153)
    file:close()
    if #source > 2097152 or source:byte(1) == 27 then error("Unsupported settings file") end
    local chunk = loadstring(source)
    if not chunk then error("Invalid Lua settings; refusing to overwrite") end
    -- Profiles may share plain tables with pairs; neither input can access IO or modules.
    setfenv(chunk, is_profile and { pairs = pairs } or {})
    local ticks = 0
    debug.sethook(function()
        ticks = ticks + 1
        if ticks > 100 then error("Settings evaluation limit exceeded") end
    end, "", 10000)
    local ok, value = pcall(chunk)
    debug.sethook()
    if not ok or type(value) ~= "table" then error("Settings must return a data table") end
    return value
end

local function sorted_keys(value)
    local keys = {}
    for key in pairs(value) do
        if type(key) ~= "string" and type(key) ~= "number" then error("Unsupported settings key") end
        keys[#keys + 1] = key
    end
    table.sort(keys, function(a, b)
        if type(a) == type(b) then return a < b end
        return type(a) < type(b)
    end)
    return keys
end

local function serialize(value, depth, seen)
    local kind = type(value)
    if kind == "string" then return string.format("%q", value) end
    if kind == "boolean" then return tostring(value) end
    if kind == "number" then
        if value ~= value or value == math.huge or value == -math.huge then error("Non-finite settings number") end
        return string.format("%.17g", value)
    end
    if kind ~= "table" or seen[value] or depth > 40 then error("Unsupported settings data") end
    seen[value] = true
    local lines = { "{" }
    for _, key in ipairs(sorted_keys(value)) do
        lines[#lines + 1] = string.rep("    ", depth + 1) .. "[" .. serialize(key, 0, {}) .. "] = "
            .. serialize(value[key], depth + 1, seen) .. ","
    end
    lines[#lines + 1] = string.rep("    ", depth) .. "}"
    seen[value] = nil
    return table.concat(lines, "\n")
end

local function main()
    local data = read_table(arg[1], arg[2] == "missing")
    local before = serialize(data, 0, {})
    local changes = {}
    local function merge(destination, desired, prefix, missing_only)
        for _, key in ipairs(sorted_keys(desired)) do
            local value = desired[key]
            local name = prefix == "" and tostring(key) or prefix .. "." .. tostring(key)
            if type(value) == "table" then
                if type(destination[key]) ~= "table" and (not missing_only or destination[key] == nil) then
                    destination[key] = {}
                    changes[name] = true
                end
                if type(destination[key]) == "table" then merge(destination[key], value, name, missing_only) end
            elseif destination[key] ~= value and (not missing_only or destination[key] == nil) then
                destination[key] = value
                changes[name] = true
            end
        end
    end
    for index = 3, #arg do
        local profile = read_table(arg[index], false, true)
        serialize(profile, 0, {}) -- Validate before traversing any profile data.
        merge(data, profile.seed or {}, "", true)
        merge(data, profile.set or {}, "", false)
        for _, path in ipairs(profile.remove or {}) do
            local parent = data
            for n = 1, #path - 1 do
                parent = type(parent) == "table" and parent[path[n]] or nil
            end
            if type(parent) == "table" and parent[path[#path]] ~= nil then
                parent[path[#path]] = nil
                changes[table.concat(path, ".")] = true
            end
        end
    end
    local after = serialize(data, 0, {})
    if before ~= after then
        -- Only managed key names go to diagnostics; never their values.
        for _, name in ipairs(sorted_keys(changes)) do io.stderr:write(name, "\n") end
        io.write("-- Managed preferences merged; other values preserved.\nreturn ", after, "\n")
    end
end

local ok = pcall(main)
if not ok then
    io.stderr:write("Cannot safely merge settings; original files were not changed.\n")
    os.exit(2)
end
