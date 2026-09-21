using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace UavGroundStation.Telemetry
{
    /// <summary>
    /// Immutable telemetry packet state representation.
    /// Uses value semantics to guarantee zero heap allocations in hot ingestion loops.
    /// </summary>
    public readonly record struct TelemetryState(
        double RollDeg,
        double PitchDeg,
        double YawDeg,
        double AltitudeMeters,
        double AirspeedMps,
        int BatteryPercentage,
        int Satellites,
        double Latitude,
        double Longitude,
        string FlightMode,
        bool IsArmed,
        long PacketCount,
        double IngestionRateHz
    );

    /// <summary>
    /// High-throughput telemetry ingestion & rate-limiting aggregator.
    /// Decouples 50+ Hz raw socket ingestion from 20 Hz UI thread rendering.
    /// </summary>
    public sealed class TelemetryAggregator : IAsyncDisposable
    {
        private readonly Channel<TelemetryState> _ingressChannel;
        private readonly PeriodicTimer _renderTimer;
        private readonly CancellationTokenSource _cts = new();
        private TelemetryState _latestSnapshot;
        private long _packetCounter;
        private readonly Stopwatch _stopwatch = Stopwatch.StartNew();

        // High-priority emergency bypass event (Bypasses the 20 Hz UI throttle)
        public event Action<string>? OnEmergencyInterrupt;

        // Throttled UI dispatch event (Invoked at locked 20 Hz)
        public event Action<TelemetryState>? OnUiTick;

        public TelemetryAggregator(int boundedCapacity = 1000, double targetUiRateHz = 20.0)
        {
            // Bounded channel with DropOldest backpressure policy
            _ingressChannel = Channel.CreateBounded<TelemetryState>(new BoundedChannelOptions(boundedCapacity)
            {
                FullMode = BoundedChannelFullMode.DropOldest,
                SingleReader = true,
                SingleWriter = false
            });

            var intervalMs = TimeSpan.FromMilliseconds(1000.0 / targetUiRateHz);
            _renderTimer = new PeriodicTimer(intervalMs);

            // Start background consumer & UI dispatcher loops
            Task.Run(ConsumeTelemetryAsync);
            Task.Run(DispatchToUiAsync);
        }

        /// <summary>
        /// Ingests incoming MAVLink/ZMQ telemetry packet from network threads.
        /// Thread-safe and non-blocking.
        /// </summary>
        public ValueTask IngestPacketAsync(TelemetryState packet)
        {
            Interlocked.Increment(ref _packetCounter);
            return _ingressChannel.Writer.WriteAsync(packet);
        }

        /// <summary>
        /// Fires an immediate safety alarm directly to the UI thread without throttling.
        /// </summary>
        public void RaiseEmergencyAlert(string alertMessage)
        {
            OnEmergencyInterrupt?.Invoke($"[SAFETY INTERRUPT] {DateTime.UtcNow:HH:mm:ss.fff} - {alertMessage}");
        }

        private async Task ConsumeTelemetryAsync()
        {
            var reader = _ingressChannel.Reader;
            while (await reader.WaitToReadAsync(_cts.Token))
            {
                while (reader.TryRead(out var state))
                {
                    // Update atomic snapshot
                    _latestSnapshot = state;

                    // Geofence / Link Failsafe check
                    if (state.AltitudeMeters > 500.0)
                    {
                        RaiseEmergencyAlert($"MAX CEILING BREACH: {state.AltitudeMeters:F1}m (Limit: 500m)");
                    }
                }
            }
        }

        private async Task DispatchToUiAsync()
        {
            while (await _renderTimer.WaitForNextTickAsync(_cts.Token))
            {
                var snapshot = _latestSnapshot;
                var elapsedSec = _stopwatch.Elapsed.TotalSeconds;
                var currentCount = Interlocked.Read(ref _packetCounter);
                var currentRate = elapsedSec > 0 ? currentCount / elapsedSec : 0.0;

                var enrichedState = snapshot with
                {
                    PacketCount = currentCount,
                    IngestionRateHz = currentRate
                };

                // Dispatch to UI listener (e.g. WPF Dispatcher or Avalonia UI thread)
                OnUiTick?.Invoke(enrichedState);
            }
        }

        public async ValueTask DisposeAsync()
        {
            _cts.Cancel();
            _ingressChannel.Writer.Complete();
            _renderTimer.Dispose();
            _cts.Dispose();
            await Task.CompletedTask;
        }
    }
}
