-- src/app/scripts/redis/reserve_feature.lua

-- KEYS[1]: a HASH key for the user's/team's shadow ledger. e.g., "shadow_ledger:user:123"
-- ARGV[1]: total usage to reserve (string decimal)
-- ARGV[2]: JSON string array of entitlement IDs to use, in order. e.g., "[101, 105]"
-- ARGV[3]: flat_amount (string decimal), for non-tiered pricing.
-- ARGV[4]: unit_count (string integer), how many units per amount.
-- ARGV[5]: JSON string of tiers array. e.g., '[{"up_to": 1000, "amount": "0.01"}, {"up_to": null, "amount": "0.008"}]'

-- Helper function to calculate cost based on tiers
-- This logic MUST perfectly mirror the Python CostCalculator._calculate_tiered_cost
local function calculate_tiered_cost(usage, tiers, unit_count)
    local remaining_usage = tonumber(usage)
    local total_cost = 0
    local previous_up_to = 0

    -- Sort tiers by 'up_to' ascending, with nulls (infinity) last.
    table.sort(tiers, function(a, b)
        local a_up_to = a.up_to and tonumber(a.up_to) or math.huge
        local b_up_to = b.up_to and tonumber(b.up_to) or math.huge
        return a_up_to < b_up_to
    end)

    for _, tier in ipairs(tiers) do
        local tier_up_to = tier.up_to and tonumber(tier.up_to) or math.huge
        local tier_amount = tonumber(tier.amount)

        if remaining_usage <= 0 then
            break
        end

        local usage_in_tier = math.min(remaining_usage, tier_up_to - previous_up_to)
        
        if usage_in_tier > 0 then
            local cost_for_tier = (usage_in_tier / unit_count) * tier_amount
            total_cost = total_cost + cost_for_tier
            remaining_usage = remaining_usage - usage_in_tier
        end

        previous_up_to = tier_up_to
    end

    -- If usage exceeds all defined tiers, use the price of the last (highest) tier for the remainder.
    if remaining_usage > 0 and #tiers > 0 then
        local last_tier_amount = tonumber(tiers[#tiers].amount)
        total_cost = total_cost + (remaining_usage / unit_count) * last_tier_amount
    end
    
    return total_cost
end


-- 1. Parse Arguments
local total_usage = tonumber(ARGV[1])
local entitlement_ids = cjson.decode(ARGV[2])
local flat_amount = tonumber(ARGV[3])
local unit_count = tonumber(ARGV[4])
local tiers = cjson.decode(ARGV[5])

local ledger = redis.call('HGETALL', KEYS[1])
local ledger_map = {}
for i = 1, #ledger, 2 do
    ledger_map[ledger[i]] = ledger[i+1]
end

local wallet_balance = tonumber(ledger_map['wallet_balance'] or '0')
local uncovered_usage = total_usage
local reserved_from_entitlements = {}

-- 2. Consume from entitlements first
if #entitlement_ids > 0 then
    for _, ent_id in ipairs(entitlement_ids) do
        if uncovered_usage <= 0 then break end
        
        local ent_key = 'entitlement:' .. ent_id
        local ent_balance = tonumber(ledger_map[ent_key] or '0')

        if ent_balance > 0 then
            local consume_from_this = math.min(uncovered_usage, ent_balance)
            redis.call('HINCRBYFLOAT', KEYS[1], ent_key, -consume_from_this)
            uncovered_usage = uncovered_usage - consume_from_this
            reserved_from_entitlements[tostring(ent_id)] = consume_from_this
        end
    end
end

-- 3. Calculate cost for the uncovered usage
local cost_from_wallet = 0
if uncovered_usage > 0 then
    if #tiers > 0 then
        -- [NEW LOGIC] Use the tiered calculator
        cost_from_wallet = calculate_tiered_cost(uncovered_usage, tiers, unit_count)
    elseif flat_amount > 0 then
        -- [OLD LOGIC] Fallback to flat pricing
        cost_from_wallet = (uncovered_usage / unit_count) * flat_amount
    else
        -- No price configured for overage, but there is overage. This is an insufficient funds situation.
        return {2, "Entitlements depleted and no pay-as-you-go price is configured for this feature."}
    end
end

-- 4. Check wallet balance and reserve
if cost_from_wallet > 0 then
    if wallet_balance >= cost_from_wallet then
        redis.call('HINCRBYFLOAT', KEYS[1], 'wallet_balance', -cost_from_wallet)
    else
        -- Not enough money, need to roll back entitlement reservations
        for ent_id, amount in pairs(reserved_from_entitlements) do
            redis.call('HINCRBYFLOAT', KEYS[1], 'entitlement:' .. ent_id, amount)
        end
        return {1, "Insufficient wallet balance...", string.format("%.10f", cost_from_wallet), cjson.encode(reserved_from_entitlements)}
    end
end

-- 5. Success
return {3, "Success", string.format("%.10f", cost_from_wallet), cjson.encode(reserved_from_entitlements)}